"""
Transit route extraction and processing service
Handles Google Maps API integration, station matching, and route parsing
"""
import os
import re
import json
import requests
from typing import List, Dict, Tuple
from pydantic import BaseModel
from datetime import date, datetime, timezone, timedelta

# Google Maps API Configuration
GOOGLE_MAPS_API_KEY = "AIzaSyDAHi8BNX3Fp3WxcOtAWg1fuzBWSBB7J4w"

def get_est_date() -> date:
    """Get current date in Eastern Standard Time"""
    # EST is UTC-5
    est_tz = timezone(timedelta(hours=-5))
    est_now = datetime.now(est_tz)
    return est_now.date()

# -------------------------------
# PYDANTIC MODELS
# -------------------------------
class ParsedRide(BaseModel):
    line: str
    boarding_stop: str
    departing_stop: str
    ride_date: date
    transferred: bool = False

# -------------------------------
# STATION MANAGEMENT
# -------------------------------
def load_subway_stations() -> dict:
    """Load subway station data from JSON file"""
    try:
        with open('data/stops.json', 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        print("⚠️ stops.json not found, falling back to empty stations")
        return {}

def clean_station_name(name: str) -> str:
    """Clean and normalize station names for matching"""
    # Convert to lowercase
    clean_name = name.lower()
    
    # Remove common street terms
    clean_name = re.sub(r'\b(street|st|avenue|ave|road|rd|boulevard|blvd|plaza|square|sq)\b', '', clean_name)
    
    # Remove transit-specific terms
    clean_name = re.sub(r'\b(station|subway|stop)\b', '', clean_name)
    
    # Remove location terms
    clean_name = re.sub(r'\b(new york|ny|brooklyn|manhattan|queens|bronx)\b', '', clean_name)
    
    # Remove address patterns like "20 W 34th St"
    clean_name = re.sub(r'\d+\s*(w|e|n|s|west|east|north|south)\s*\d+\w*', '', clean_name)
    
    # Remove all non-alphanumeric characters except spaces
    clean_name = re.sub(r'[^\w\s]', ' ', clean_name)
    
    # Normalize whitespace
    clean_name = re.sub(r'\s+', ' ', clean_name).strip()
    
    return clean_name

def find_matching_stations(extracted_name: str, all_stations: list) -> list:
    """Find matching subway stations with confidence scores"""
    if not extracted_name:
        return []
    
    cleaned_extracted = clean_station_name(extracted_name)
    matches = []
    
    for station in all_stations:
        cleaned_station = clean_station_name(station)
        
        # Exact match (highest confidence)
        if cleaned_extracted == cleaned_station:
            matches.append((station, 100))
            continue
        
        # Partial match (medium confidence)
        if cleaned_extracted in cleaned_station or cleaned_station in cleaned_extracted:
            matches.append((station, 80))
            continue
        
        # Word-based matching (lower confidence)
        extracted_words = set(cleaned_extracted.split())
        station_words = set(cleaned_station.split())
        
        if extracted_words and station_words:
            overlap = len(extracted_words.intersection(station_words))
            total_words = min(len(extracted_words), len(station_words))
            
            if overlap > 0 and total_words > 0:
                confidence = int((overlap / total_words) * 60)
                if confidence >= 30:  # Only include matches with reasonable confidence
                    matches.append((station, confidence))
    
    # Sort by confidence score (highest first)
    matches.sort(key=lambda x: x[1], reverse=True)
    
    # Return top 3 matches
    return matches[:3]

def normalize_stop_name(stop_name: str) -> str:
    """Normalize stop names for consistency"""
    # Remove extra whitespace and capitalize properly
    normalized = ' '.join(stop_name.split())
    # Handle common abbreviations
    normalized = normalized.replace(' St ', ' St-').replace(' Av ', ' Ave ')
    return normalized

# -------------------------------
# URL EXPANSION AND COORDINATE EXTRACTION
# -------------------------------
def expand_shortened_url(url: str) -> str:
    """Expand shortened Google Maps URLs to get the full URL with coordinates"""
    try:
        # Check if it's a shortened URL
        if 'maps.app.goo.gl' in url or 'goo.gl' in url:
            print(f"🔗 Expanding shortened URL: {url}")
            
            # Use requests to follow redirects and get the expanded URL
            response = requests.get(url, allow_redirects=True, timeout=10)
            expanded_url = response.url
            print(f"✅ Expanded to: {expanded_url}")
            return expanded_url
        else:
            # URL is already expanded
            return url
            
    except Exception as e:
        print(f"⚠️ Error expanding URL: {e}")
        return url  # Return original URL if expansion fails

# -------------------------------
# GOOGLE MAPS API - NEW SIMPLIFIED APPROACH
# -------------------------------

def get_transit_rides_from_api(api_key: str, origin: str, destination: str):
    """
    Calls Google Directions API and extracts individual transit rides.
    Returns simplified ride data: board_stop, depart_stop, line
    """
    print(f"\n🚇 Calling Google Directions API...")
    print(f"📍 Origin: {origin}")
    print(f"📍 Destination: {destination}")
    
    url = "https://maps.googleapis.com/maps/api/directions/json"

    params = {
        "origin": origin,
        "destination": destination,
        "mode": "transit",
        "transit_mode": "subway",
        "key": api_key,
        "alternatives": "true"
    }
    
    print(f"🌐 API URL: {url}")
    print(f"📋 Parameters: {params}")

    resp = requests.get(url, params=params)
    data = resp.json()
    
    print(f"📊 API Response status: {data.get('status')}")

    if data.get("status") != "OK":
        print("❌ API Error:", data.get("status"), data.get("error_message"))
        return []

    rides = []
    routes = data.get("routes", [])
    print(f"🛣️ Found {len(routes)} route(s)")

    # Use the first route (typically the recommended one)
    if routes:
        route = routes[0]
        print(f"\n🔄 Processing Primary Route:")
        legs = route.get("legs", [])
        for leg_idx, leg in enumerate(legs):
            print(f"  📍 Leg {leg_idx + 1}: {leg.get('start_address')} → {leg.get('end_address')}")
            steps = leg.get("steps", [])
            for step_idx, step in enumerate(steps):
                transit_details = step.get("transit_details")
                if transit_details:
                    departure_stop = transit_details["departure_stop"]
                    arrival_stop = transit_details["arrival_stop"]
                    line_info = transit_details["line"]
                    
                    # Extract simplified ride information matching DB schema
                    ride = {
                        "board_stop": departure_stop["name"],
                        "depart_stop": arrival_stop["name"], 
                        "line": line_info.get("short_name", line_info.get("name", "Unknown")).replace(" Line", "")
                    }
                    rides.append(ride)
                    print(f"    🚇 Ride: {ride['board_stop']} → {ride['depart_stop']} (Line: {ride['line']})")
                else:
                    # Walking step
                    if step.get("travel_mode") == "WALKING":
                        print(f"    🚶 Walk: {step.get('html_instructions', 'Walking segment')}")

    return rides

def extract_origin_destination(maps_url: str):
    """
    Extracts the origin and destination from a Google Maps URL
    such as: https://www.google.com/maps/dir/Origin/Destination/
    """
    import urllib.parse
    
    # URL path after domain
    parsed = urllib.parse.urlparse(maps_url)
    path = parsed.path
    parts = path.split("/")
    
    print(f"🗺️ URL path: {path}")
    print(f"🔍 Path parts: {parts}")

    try:
        # Find the index of "dir"
        i = parts.index("dir")
        origin = urllib.parse.unquote(parts[i + 1])
        destination = urllib.parse.unquote(parts[i + 2])
        print(f"📍 Raw origin: {origin}")
        print(f"📍 Raw destination: {destination}")
        return origin, destination
    except (ValueError, IndexError) as e:
        print(f"❌ Could not parse origin/destination from URL: {e}")
        raise ValueError("Could not parse origin/destination from URL.")

async def extract_transit_info_with_new_api(url: str) -> List[ParsedRide]:
    """Extract transit information using simplified Google Maps API approach"""
    try:
        # Step 1: Expand shortened URLs if needed
        expanded_url = expand_shortened_url(url)
        print(f"🔗 Expanded URL: {expanded_url}")
        
        # Step 2: Extract origin and destination from URL
        origin, destination = extract_origin_destination(expanded_url)
        
        print(f"📍 Origin: {origin}")
        print(f"📍 Destination: {destination}")
        
        # Step 3: Get transit rides from API
        api_rides = get_transit_rides_from_api(GOOGLE_MAPS_API_KEY, origin, destination)
        
        # Step 4: Convert to ParsedRide objects with proper schema
        parsed_rides = []
        for ride in api_rides:
            parsed_ride = ParsedRide(
                line=ride["line"],
                boarding_stop=ride["board_stop"],  # Correct mapping: API "board_stop" -> model "boarding_stop"
                departing_stop=ride["depart_stop"],  # Correct mapping: API "depart_stop" -> model "departing_stop"
                ride_date=get_est_date(),  # Use EST date instead of UTC
                transferred=False
            )
            parsed_rides.append(parsed_ride)
        
        # Step 5: Detect transfers between consecutive rides
        return detect_transfers_in_rides(parsed_rides)
        
    except Exception as e:
        print(f"❌ Error with new Google Maps API approach: {e}")
        # Fallback to existing method
        return await extract_transit_info_with_api(url)

# -------------------------------
# GOOGLE MAPS API - ORIGINAL METHODS
# -------------------------------
async def extract_transit_info_with_api(url: str) -> List[ParsedRide]:
    """Extract transit information using Google Maps Directions API"""
    try:
        # Use the new simplified approach first
        return await extract_transit_info_with_new_api(url)
        
    except Exception as e:
        print(f"❌ Error with simplified API approach: {e}")
        
        # Original fallback approach with coordinates parsing
        try:
            # Step 1: Expand shortened URLs if needed
            expanded_url = expand_shortened_url(url)  # Remove await since function is no longer async
            print(f"🔗 Expanded URL: {expanded_url}")
            
            # Step 2: Try to parse origin and destination from the expanded URL
            origin, destination = parse_google_maps_url(expanded_url)
            
            # Step 3: If URL parsing fails, return error - no browser fallback
            if not origin or not destination:
                print("❌ Could not parse coordinates from URL, and browser automation is disabled")
                return []  # Return empty list instead of browser fallback
            
            print(f"📍 Origin: {origin}")
            print(f"📍 Destination: {destination}")
            
            # Step 4: Make request to Google Maps Directions API
            api_url = "https://maps.googleapis.com/maps/api/directions/json"
            params = {
                'origin': f"{origin[0]},{origin[1]}" if isinstance(origin, tuple) else origin,
                'destination': f"{destination[0]},{destination[1]}" if isinstance(destination, tuple) else destination,
                'mode': 'transit',
                'transit_mode': 'subway',
                'key': GOOGLE_MAPS_API_KEY,
                'alternatives': 'true',
                'units': 'metric'
            }
            
            response = requests.get(api_url, params=params)
            data = response.json()
            
            if data.get('status') != 'OK':
                print(f"❌ Google Maps API error: {data.get('status')} - {data.get('error_message', 'Unknown error')}")
                if data.get('status') == 'REQUEST_DENIED':
                    print("💡 Make sure to enable the Directions API in your Google Cloud Console")
                return []  # Return empty list instead of browser fallback
            
            routes = data.get('routes', [])
            if not routes:
                print("❌ No transit routes found")
                return []  # Return empty list instead of browser fallback
            
            print(f"✅ Found {len(routes)} route(s) from Google Maps API")
            
            print(f"✅ Found {len(routes)} route(s) from Google Maps API")
            
            # Process the API routes
            parsed_rides = process_api_routes(routes)
            return detect_transfers_in_rides(parsed_rides)
            
        except Exception as fallback_error:
            print(f"❌ Error with original API approach: {fallback_error}")
            # Return empty list instead of browser fallback
            return []
        
    except Exception as e:
        print(f"❌ Error with Google Maps API: {e}")
        # Return empty list instead of browser fallback
        return []

def process_api_routes(routes: list) -> List[ParsedRide]:
    """Process Google Maps API routes into ParsedRide objects"""
    parsed_rides = []
    
    for route_idx, route in enumerate(routes):
        legs = route.get('legs', [])
        
        for leg in legs:
            steps = leg.get('steps', [])
            
            for step in steps:
                if step.get('travel_mode') == 'TRANSIT':
                    transit_details = step.get('transit_details', {})
                    line_info = transit_details.get('line', {})
                    
                    # Extract line information
                    line_name = line_info.get('short_name', '')
                    if not line_name:
                        line_name = line_info.get('name', '')
                    
                    # Extract stop information
                    departure_stop = transit_details.get('departure_stop', {}).get('name', '')
                    arrival_stop = transit_details.get('arrival_stop', {}).get('name', '')
                    
                    if line_name and departure_stop and arrival_stop:
                        parsed_ride = ParsedRide(
                            line=line_name,
                            boarding_stop=normalize_stop_name(departure_stop),
                            departing_stop=normalize_stop_name(arrival_stop),
                            ride_date=date.today(),
                            transferred=False  # Will be detected later
                        )
                        parsed_rides.append(parsed_ride)
    
    return parsed_rides

def detect_transfers_in_rides(rides: List[ParsedRide]) -> List[ParsedRide]:
    """Detect and mark transfers between consecutive rides"""
    for i in range(len(rides) - 1):
        current_ride = rides[i]
        next_ride = rides[i + 1]
        
        # If the departing stop of current ride is similar to boarding stop of next ride
        if similar_station_names(current_ride.departing_stop, next_ride.boarding_stop):
            current_ride.transferred = True
    
    return rides

def similar_station_names(name1: str, name2: str) -> bool:
    """Check if two station names are similar (for transfer detection)"""
    if not name1 or not name2:
        return False
    
    # Normalize both names
    norm1 = clean_station_name(name1)
    norm2 = clean_station_name(name2)
    
    # Check for exact match
    if norm1 == norm2:
        return True
    
    # Check for partial overlap
    words1 = set(norm1.split())
    words2 = set(norm2.split())
    
    return len(words1.intersection(words2)) >= 1

# -------------------------------
# URL PARSING UTILITIES
# -------------------------------
def parse_google_maps_url(url: str) -> tuple:
    """Parse Google Maps URL to extract origin and destination coordinates"""
    from urllib.parse import urlparse, unquote, parse_qs
    
    try:
        parsed = urlparse(url)
        print(f"🔍 Parsing URL path: {parsed.path}")
        
        # Handle different Google Maps URL formats
        if '/dir/' in parsed.path:
            # Format: /maps/dir/origin/destination/ or /dir/origin/destination/
            path_parts = parsed.path.split('/')
            print(f"🔍 Path parts: {path_parts}")
            
            # Find the index of 'dir' to get the right positions
            dir_index = -1
            for i, part in enumerate(path_parts):
                if part == 'dir':
                    dir_index = i
                    break
            
            if dir_index >= 0 and len(path_parts) > dir_index + 2:
                origin_str = unquote(path_parts[dir_index + 1])
                dest_str = unquote(path_parts[dir_index + 2])
                print(f"📍 Extracted origin: {origin_str}")
                print(f"📍 Extracted destination: {dest_str}")
                return origin_str, dest_str
        
        # Handle query parameters
        query_params = parse_qs(parsed.query)
        
        # Check for saddr/daddr parameters
        if 'saddr' in query_params and 'daddr' in query_params:
            return query_params['saddr'][0], query_params['daddr'][0]
        
        # Check for origin/destination parameters
        if 'origin' in query_params and 'destination' in query_params:
            return query_params['origin'][0], query_params['destination'][0]
        
        # Handle fragment-based coordinates
        if parsed.fragment:
            coords = extract_coords_from_fragment(parsed.fragment)
            if coords:
                return coords
        
        # Handle data parameter
        if 'data' in query_params:
            coords = extract_coords_from_data(query_params['data'][0])
            if coords:
                return coords
        
        print(f"⚠️ Could not parse coordinates from URL: {url}")
        return None, None
        
    except Exception as e:
        print(f"❌ Error parsing URL: {e}")
        return None, None

def extract_coords_from_fragment(fragment: str) -> tuple:
    """Extract coordinates from URL fragment"""
    # Look for coordinate patterns in the fragment
    coord_pattern = r'(-?\d+\.?\d*),(-?\d+\.?\d*)'
    matches = re.findall(coord_pattern, fragment)
    
    if len(matches) >= 2:
        # Assuming first match is origin, second is destination
        origin = (float(matches[0][0]), float(matches[0][1]))
        destination = (float(matches[1][0]), float(matches[1][1]))
        return origin, destination
    
    return None, None

def extract_coords_from_data(data_param: str) -> tuple:
    """Extract coordinates from data parameter"""
    from urllib.parse import unquote
    try:
        # The data parameter is often URL-encoded
        data_str = unquote(data_param)
        
        # Look for coordinate patterns
        coord_pattern = r'(-?\d+\.?\d*),(-?\d+\.?\d*)'
        matches = re.findall(coord_pattern, data_str)
        
        if len(matches) >= 2:
            origin = (float(matches[0][0]), float(matches[0][1]))
            destination = (float(matches[1][0]), float(matches[1][1]))
            return origin, destination
            
    except Exception as e:
        print(f"Error parsing data parameter: {e}")
    
    return None, None

# All browser automation and legacy fallback functions removed
# The service now relies entirely on Google Maps API for URL parsing