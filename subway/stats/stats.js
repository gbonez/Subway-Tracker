// Configuration
const backendUrl = `https://subway-tracker-production.up.railway.app`;
console.log('🔧 Backend URL configured as:', backendUrl);
console.log('🌐 Current page protocol:', window.location.protocol);
console.log('🌐 Current page host:', window.location.host);
let currentFilter = 'all';
let charts = {};
let customStartDate = null;
let customEndDate = null;

// Get current EST date
function getESTDate() {
    const now = new Date();
    const estOffset = -5 * 60; // EST is UTC-5 (in minutes)
    const estDate = new Date(now.getTime() + (estOffset - now.getTimezoneOffset()) * 60000);
    return estDate;
}

// Format date for display
function formatDateForDisplay(date) {
    return date.toLocaleDateString('en-US', {
        timeZone: 'America/New_York',
        month: 'short',
        day: 'numeric',
        year: '2-digit'
    });
}

// Format date range for display
function formatDateRange(startDate, endDate, filterType) {
    switch (filterType) {
        case 'day':
            return `Daily: ${formatDateForDisplay(endDate)}`;
        case 'week':
            return `Weekly: ${formatDateForDisplay(startDate)} - ${formatDateForDisplay(endDate)}`;
        case 'month':
            return `Monthly: ${endDate.toLocaleDateString('en-US', {
                timeZone: 'America/New_York',
                month: 'long',
                year: '2-digit'
            })}`;
        case 'year':
            return `Yearly: ${endDate.getFullYear()}`;
        case 'custom':
            return `Custom: ${formatDateForDisplay(startDate)} - ${formatDateForDisplay(endDate)}`;
        default:
            return 'All Time';
    }
}

// Update date range display
function updateDateRangeDisplay() {
    const display = document.getElementById('dateRangeDisplay');
    const dateInfo = getDateFilter();

    if (dateInfo && dateInfo.start && dateInfo.end) {
        const startDate = new Date(dateInfo.start);
        const endDate = new Date(dateInfo.end);
        display.textContent = formatDateRange(startDate, endDate, currentFilter);
    } else {
        display.textContent = 'All Time';
    }
}

// Chart color schemes
const colors = {
    primary: '#4CAF50',
    secondary: '#2196F3',
    accent: '#FF9800',
    danger: '#F44336',
    warning: '#FFC107',
    info: '#00BCD4',
    gradient: [
        '#4CAF50', '#2196F3', '#FF9800', '#F44336', '#9C27B0',
        '#607D8B', '#795548', '#3F51B5', '#009688', '#FFC107'
    ]
};

// Official MTA line colors
const mtaLineColors = {
    '1': '#EE352E', // Red
    '2': '#EE352E', // Red
    '3': '#EE352E', // Red
    '4': '#00933C', // Green
    '5': '#00933C', // Green
    '6': '#00933C', // Green
    '6X': '#00933C', // Green (6 Express)
    '7': '#B933AD', // Purple
    '7X': '#B933AD', // Purple (7 Express)
    'A': '#0039A6', // Blue
    'B': '#FF6319', // Orange
    'C': '#0039A6', // Blue
    'D': '#FF6319', // Orange
    'E': '#0039A6', // Blue
    'F': '#FF6319', // Orange
    'FX': '#FF6319', // Orange (F Express)
    'G': '#6CBE45', // Light Green
    'H': '#0039A6', // Blue (Shuttle)
    'J': '#996633', // Brown
    'L': '#A7A9AC', // Gray
    'M': '#FF6319', // Orange
    'N': '#FCCC0A', // Yellow
    'Q': '#FCCC0A', // Yellow
    'R': '#FCCC0A', // Yellow
    'S': '#808183', // Gray (Shuttle)
    'T': '#00ADD0', // Teal
    'W': '#FCCC0A', // Yellow
    'Z': '#996633', // Brown
    'SIR': '#0039A6' // Blue (Staten Island Railway)
};

// Borough mapping for subway stations
const boroughMapping = {
    'Manhattan': [
        '103 St', '116 St-Columbia University', '125 St', '137 St-City College', '14 St', '14 St-Union Sq',
        '145 St', '157 St', '168 St-Washington Hts', '18 St', '181 St', '191 St', '207 St', '215 St',
        '23 St', '231 St', '238 St', '28 St', '34 St-Penn Station', '34 St-Herald Sq', '42 St-Bryant Pk',
        '47-50 Sts-Rockefeller Ctr', '5 Ave', '5 Ave/53 St', '5 Ave/59 St', '50 St', '51 St', '52 St',
        '53 St', '57 St', '57 St-7 Ave', '59 St', '59 St-Columbus Circle', '66 St-Lincoln Center',
        '68 St-Hunter College', '72 St', '77 St', '79 St', '8 St-NYU', '86 St', '96 St',
        'Astor Pl', 'Bleecker St', 'Bowling Green', 'Broadway-Lafayette St', 'Brooklyn Bridge-City Hall',
        'Canal St', 'Cathedral Pkwy (110 St)', 'Central Park North (110 St)', 'Chambers St',
        'Christopher St-Stonewall', 'City Hall', 'Cortlandt St', 'Delancey St-Essex St', 'Dyckman St',
        'East Broadway', 'Franklin St', 'Fulton St', 'Grand Central-42 St', 'Houston St', 'Lexington Ave/53 St',
        'Lexington Ave/63 St', 'Marble Hill-225 St', 'Rector St', 'South Ferry', 'Spring St',
        'Times Sq-42 St', 'Van Cortlandt Park-242 St', 'W 4 St-Wash Sq', 'Wall St', 'WTC Cortlandt',
        'Whitehall St-South Ferry', 'World Trade Center', '33 St', '116 St', '135 St', 'Harlem-148 St',
        '2 Ave', '7 Ave', 'Bowery'
    ],
    'Brooklyn': [
        'Atlantic Ave-Barclays Ctr', 'Bergen St', 'Beverly Rd', 'Borough Hall', 'Church Ave', 'Clark St',
        'Crown Hts-Utica Ave', 'Eastern Pkwy-Brooklyn Museum', 'Flatbush Ave-Brooklyn College',
        'Franklin Ave-Medgar Evers College', 'Grand Army Plaza', 'Hoyt St', 'Nevins St',
        'Newkirk Ave-Little Haiti', 'Park Place', 'President St-Medgar Evers College', 'Sterling St',
        'Winthrop St', '15 St-Prospect Park', '18 Ave', '4 Ave-9 St', 'Avenue I', 'Avenue N',
        'Avenue P', 'Avenue U', 'Avenue X', 'Bay Pkwy', 'Carroll St', 'Coney Island-Stillwell Ave',
        'Ditmas Ave', 'Fort Hamilton Pkwy', 'Jay St-MetroTech', 'Kings Hwy', 'Neptune Ave',
        'Smith-9 Sts', 'W 8 St-NY Aquarium', 'York St', '25 St', '36 St', '45 St', '49 St',
        '77 St', 'Bay Ridge Ave', 'Bay Ridge-95 St', 'Court St', 'DeKalb Ave', 'Prospect Heights',
        'Greenpoint Ave', 'Nassau Ave', 'New Utrecht Ave', '20 Ave', '25 Ave', '86 St', 'Prospect Park',
        'Franklin Ave', 'Nostrand Ave', 'Junius St', 'Kingston Ave', 'New Lots Ave', 'Pennsylvania Ave',
        'Rockaway Ave', 'Saratoga Ave', 'Sutter Ave-Rutland Rd', 'Van Siclen Ave', 'Chauncey St',
        'Gates Ave', 'Halsey St', 'Kosciuszko St', 'Myrtle Ave', 'Broadway Junction', 'Alabama Ave',
        '75 St-Elderts Ln', '85 St-Forest Pkwy', 'Cypress Hills', 'Norwood Ave', 'Cleveland St',
        'Crescent St', 'Hewes St', 'Lorimer St', 'Marcy Ave', 'Myrtle-Wyckoff Aves', 'Seneca Ave',
        'Fresh Pond Rd', 'Middle Village-Metropolitan Ave', 'Metropolitan Ave', 'Grand Ave-Newtown',
        'Montrose Ave', 'Morgan Ave', 'Jefferson St', 'DeKalb Ave', 'Flushing Ave', 'Clinton-Washington Aves',
        'Classon Ave', 'Botanic Garden'
    ],
    'Queens': [
        '103 St-Corona Plaza', '111 St', '33 St-Rawson St', '34 St-Hudson Yards', '40 St-Lowery St',
        '46 St-Bliss St', '61 St-Woodside', '69 St', '74 St-Broadway', '82 St-Jackson Hts',
        '90 St-Elmhurst Ave', 'Court Sq', 'Court Sq-23 St', 'Flushing-Main St', 'Hunters Point Ave',
        'Junction Blvd', 'Mets-Willets Point', 'Queensboro Plaza', 'Vernon Blvd-Jackson Ave',
        '63 Dr-Rego Park', '67 Ave', 'Central Ave', 'Elmhurst Ave', 'Forest Ave', 'Forest Hills-71 Ave',
        'Jackson Hts-Roosevelt Ave', 'Knickerbocker Ave', 'Northern Blvd', 'Queens Plaza',
        'Steinway St', 'Woodhaven Blvd', '104 St', '121 St', 'Jamaica Center-Parsons/Archer',
        'Sutphin Blvd-Archer Ave-JFK Airport', 'Sutphin Blvd', '169 St', '21 St-Queensbridge',
        '75 Ave', 'Briarwood', 'Jamaica-179 St', 'Kew Gardens-Union Tpke', 'Parsons Blvd',
        'Roosevelt Island', '65 St', 'Grand Ave-Newtown', 'Woodhaven Blvd',
        'Beach 105 St', 'Beach 90 St', 'Beach 98 St', 'Broad Channel', 'Rockaway Park-Beach 116 St'
    ],
    'The Bronx': [
        '149 St-Grand Concourse', '174 St', '219 St', '225 St', '233 St', '3 Ave-149 St',
        'Allerton Ave', 'Bronx Park East', 'Burke Ave', 'E 180 St', 'Freeman St', 'Gun Hill Rd',
        'Intervale Ave', 'Jackson Ave', 'Nereid Ave', 'Pelham Pkwy', 'Prospect Ave', 'Simpson St',
        'Wakefield-241 St', 'West Farms Sq-E Tremont Ave', '138 St-Grand Concourse', '161 St-Yankee Stadium',
        '167 St', '170 St', '176 St', '183 St', 'Bedford Park Blvd-Lehman College', 'Burnside Ave',
        'Fordham Rd', 'Kingsbridge Rd', 'Mosholu Pkwy', 'Mt Eden Ave', 'Woodlawn', '3 Ave-138 St',
        'Brook Ave', 'Cypress Ave', 'E 143 St-St Mary\'s St', 'E 149 St', 'Elder Ave', 'Hunts Point Ave',
        'Longwood Ave', 'Middletown Rd', 'Morrison Ave-Soundview', 'Parkchester', 'Pelham Bay Park',
        'St Lawrence Ave', 'Westchester Sq-E Tremont Ave', 'Whitlock Ave', 'Zerega Ave',
        'Baychester Ave', 'Eastchester-Dyre Ave', 'Morris Park', 'Buhre Ave', 'Castle Hill Ave'
    ],
    'Staten Island': [
        'Annadale', 'Arthur Kill', 'Bay Terrace', 'Clifton', 'Dongan Hills', 'Eltingville',
        'Grant City', 'Grasmere', 'Great Kills', 'Huguenot', 'Jefferson Ave', 'New Dorp',
        'Oakwood Heights', 'Old Town', 'Pleasant Plains', 'Prince\'s Bay', 'Richmond Valley',
        'St George', 'Stapleton', 'Tompkinsville', 'Tottenville'
    ]
};

// Get borough for a station
function getStationBorough(stationName) {
    for (const [borough, stations] of Object.entries(boroughMapping)) {
        if (stations.some(station =>
            stationName.toLowerCase().includes(station.toLowerCase()) ||
            station.toLowerCase().includes(stationName.toLowerCase()) ||
            stationName === station
        )) {
            return borough;
        }
    }
    return 'Unknown';
}

// Borough colors for pie chart
const boroughColors = {
    'Manhattan': '#EE352E', // Red
    'Brooklyn': '#0039A6', // Blue  
    'Queens': '#B933AD', // Purple
    'The Bronx': '#00933C', // Green
    'Staten Island': '#FCCC0A', // Yellow
    'Unknown': '#808183'
};// Get MTA color for a line
function getMTAColor(line) {
    return mtaLineColors[line] || '#808183'; // Default to gray if line not found
}

// Shorten station names for display
function shortenStationName(name, maxLength = 20) {
    if (name.length <= maxLength) {
        return name;
    }
    return name.substring(0, maxLength - 3) + '...';
}

// Load transfer stations mapping
let transferStationsMapping = {};
async function loadTransferStations() {
    try {
        const response = await fetch('../../data/transfer_stations.json');
        transferStationsMapping = await response.json();
        console.log('✅ Loaded transfer stations mapping');
    } catch (error) {
        console.error('❌ Failed to load transfer stations mapping:', error);
        transferStationsMapping = {};
    }
}

// Find which transfer complex a station belongs to
function findTransferComplex(stationName) {
    for (const [complexId, complex] of Object.entries(transferStationsMapping)) {
        if (complex.station_names.some(name =>
            stationName.toLowerCase().includes(name.toLowerCase()) ||
            name.toLowerCase().includes(stationName.toLowerCase()) ||
            stationName === name
        )) {
            return {
                id: complexId,
                name: complex.complex_name,
                lines: complex.lines
            };
        }
    }
    return null;
}

// Consolidate stops that serve multiple lines
function consolidateStopsByLines(stopData, rides) {
    const consolidatedStops = {};
    const stopLineUsage = {};

    // First, track which lines are used at each stop and how frequently
    rides.forEach(ride => {
        const boardStop = ride.board_stop;
        const departStop = ride.depart_stop;
        const line = ride.line;

        [boardStop, departStop].forEach(stop => {
            if (stop) {
                if (!stopLineUsage[stop]) {
                    stopLineUsage[stop] = {};
                }
                stopLineUsage[stop][line] = (stopLineUsage[stop][line] || 0) + 1;
            }
        });
    });

    // Process the stop data and consolidate by transfer complexes
    stopData.forEach(item => {
        const stopName = item.stop_name;
        const count = item.visit_count || item.transfer_count;

        // Check if this station is part of a transfer complex
        const transferComplex = findTransferComplex(stopName);
        const consolidationKey = transferComplex ? transferComplex.name : stopName;

        // Find the most frequently used line at this stop
        let primaryLine = null;
        let maxUsage = 0;
        let allLinesAtStop = [];

        if (stopLineUsage[stopName]) {
            for (const [line, usage] of Object.entries(stopLineUsage[stopName])) {
                allLinesAtStop.push(line);
                if (usage > maxUsage) {
                    maxUsage = usage;
                    primaryLine = line;
                }
            }
        }

        // If it's a transfer complex, use the most prominent line from the complex
        if (transferComplex) {
            // Find the most used line from the transfer complex's available lines
            let bestLine = primaryLine;
            let bestLineUsage = maxUsage;

            for (const complexLine of transferComplex.lines) {
                if (stopLineUsage[stopName] && stopLineUsage[stopName][complexLine] > bestLineUsage) {
                    bestLine = complexLine;
                    bestLineUsage = stopLineUsage[stopName][complexLine];
                }
            }
            primaryLine = bestLine || transferComplex.lines[0]; // Fallback to first line in complex
        }

        // Use the primary line or default
        const lineForColor = primaryLine || 'S'; // Default to shuttle gray

        if (!consolidatedStops[consolidationKey]) {
            consolidatedStops[consolidationKey] = {
                stop_name: consolidationKey,
                count: count,
                primary_line: lineForColor,
                lines: transferComplex ? transferComplex.lines : allLinesAtStop,
                color: getMTAColor(lineForColor),
                is_transfer_complex: !!transferComplex,
                original_stations: [stopName]
            };
        } else {
            // Consolidate counts and track original stations
            consolidatedStops[consolidationKey].count += count;
            if (!consolidatedStops[consolidationKey].original_stations.includes(stopName)) {
                consolidatedStops[consolidationKey].original_stations.push(stopName);
            }

            // Merge lines arrays to avoid duplicates
            if (transferComplex) {
                // Use transfer complex lines
                consolidatedStops[consolidationKey].lines = [...new Set([...consolidatedStops[consolidationKey].lines, ...transferComplex.lines])];
            } else {
                // Add any new lines from this station
                consolidatedStops[consolidationKey].lines = [...new Set([...consolidatedStops[consolidationKey].lines, ...allLinesAtStop])];
            }

            // Update primary line if this station has higher usage for a line
            if (stopLineUsage[stopName]) {
                for (const [line, usage] of Object.entries(stopLineUsage[stopName])) {
                    const totalUsageForThisLine = Object.values(consolidatedStops[consolidationKey].original_stations)
                        .reduce((sum, station) => sum + (stopLineUsage[station]?.[line] || 0), 0);

                    if (totalUsageForThisLine > maxUsage) {
                        maxUsage = totalUsageForThisLine;
                        consolidatedStops[consolidationKey].primary_line = line;
                        consolidatedStops[consolidationKey].color = getMTAColor(line);
                    }
                }
            }
        }
    });

    const result = Object.values(consolidatedStops).sort((a, b) => b.count - a.count);
    console.log('🔄 Consolidated', stopData.length, 'stations into', result.length, 'entries');
    return result;
}

// Consolidate line data and get colors
function processLineData(lineData) {
    return lineData.map(item => ({
        ...item,
        color: getMTAColor(item.line)
    }));
}

// Initialize page
document.addEventListener('DOMContentLoaded', async () => {
    setupFilterButtons();
    setupCustomDateRange();
    await loadTransferStations(); // Load transfer stations mapping first
    await loadAllData();
});

// Setup filter button functionality
function setupFilterButtons() {
    document.querySelectorAll('.filter-button').forEach(button => {
        button.addEventListener('click', async () => {
            const filterType = button.dataset.filter;

            if (filterType === 'custom') {
                // Show custom date range inputs
                document.getElementById('customDateRange').style.display = 'block';
                return;
            }

            // Hide custom date range if showing
            document.getElementById('customDateRange').style.display = 'none';

            // Update active button
            document.querySelectorAll('.filter-button').forEach(b => b.classList.remove('active'));
            button.classList.add('active');

            // Update current filter and reload data
            currentFilter = filterType;
            customStartDate = null;
            customEndDate = null;

            updateDateRangeDisplay();
            await loadAllData();
        });
    });
}

// Setup custom date range functionality
function setupCustomDateRange() {
    const startDateInput = document.getElementById('startDate');
    const endDateInput = document.getElementById('endDate');
    const applyButton = document.getElementById('applyCustomRange');
    const cancelButton = document.getElementById('cancelCustomRange');

    // Set default dates (last 30 days)
    const today = getESTDate();
    const thirtyDaysAgo = new Date(today);
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

    startDateInput.value = thirtyDaysAgo.toISOString().split('T')[0];
    endDateInput.value = today.toISOString().split('T')[0];

    applyButton.addEventListener('click', async () => {
        const startDate = startDateInput.value;
        const endDate = endDateInput.value;

        if (!startDate || !endDate) {
            alert('Please select both start and end dates');
            return;
        }

        if (new Date(startDate) > new Date(endDate)) {
            alert('Start date must be before end date');
            return;
        }

        customStartDate = startDate;
        customEndDate = endDate;
        currentFilter = 'custom';

        // Update active button
        document.querySelectorAll('.filter-button').forEach(b => b.classList.remove('active'));
        document.querySelector('[data-filter="custom"]').classList.add('active');

        // Hide custom range inputs
        document.getElementById('customDateRange').style.display = 'none';

        updateDateRangeDisplay();
        await loadAllData();
    });

    cancelButton.addEventListener('click', () => {
        document.getElementById('customDateRange').style.display = 'none';
    });
}

// Get date filter for API calls
function getDateFilter() {
    if (currentFilter === 'custom' && customStartDate && customEndDate) {
        return {
            start: customStartDate,
            end: customEndDate
        };
    }

    const now = getESTDate();
    let start;

    switch (currentFilter) {
        case 'day':
            start = new Date(now);
            start.setHours(0, 0, 0, 0);
            return {
                start: start.toISOString().split('T')[0],
                end: now.toISOString().split('T')[0]
            };
        case 'week':
            start = new Date(now);
            // Get start of current week (Sunday = 0, Monday = 1, etc.)
            // In the US, week typically starts on Sunday, so we'll use that
            const dayOfWeek = start.getDay(); // 0 = Sunday, 1 = Monday, etc.
            start.setDate(start.getDate() - dayOfWeek);
            start.setHours(0, 0, 0, 0);
            return {
                start: start.toISOString().split('T')[0],
                end: now.toISOString().split('T')[0]
            };
        case 'month':
            start = new Date(now);
            start.setMonth(now.getMonth() - 1);
            return {
                start: start.toISOString().split('T')[0],
                end: now.toISOString().split('T')[0]
            };
        case 'year':
            start = new Date(now);
            start.setFullYear(now.getFullYear() - 1);
            return {
                start: start.toISOString().split('T')[0],
                end: now.toISOString().split('T')[0]
            };
        case 'all':
        default:
            return null; // No filter for 'all'
    }
}

// Load all data and update charts
async function loadAllData() {
    try {
        showLoading();
        const dateFilter = getDateFilter();

        let params = '';
        if (dateFilter) {
            if (dateFilter.start && dateFilter.end) {
                params = `?start=${dateFilter.start}&end=${dateFilter.end}`;
            } else {
                params = `?since=${dateFilter}`;
            }
        }

        console.log('🔍 Loading data with URLs:');
        console.log(`- Rides: ${backendUrl}/rides/${params}&per_page=1000`);
        console.log(`- Visited: ${backendUrl}/stats/visited-stops${params}`);
        console.log(`- Transfers: ${backendUrl}/stats/transfer-stops${params}`);
        console.log(`- Lines: ${backendUrl}/stats/popular-lines${params}`);

        // Fetch all data with proper trailing slashes and error handling
        const [ridesResponse, visitedResponse, transfersResponse, linesResponse] = await Promise.all([
            fetch(`${backendUrl}/rides/${params}${params ? '&' : '?'}per_page=1000`, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                },
                mode: 'cors'
            }),
            fetch(`${backendUrl}/stats/visited-stops${params}`, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                },
                mode: 'cors'
            }),
            fetch(`${backendUrl}/stats/transfer-stops${params}`, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                },
                mode: 'cors'
            }),
            fetch(`${backendUrl}/stats/popular-lines${params}`, {
                method: 'GET',
                headers: {
                    'Accept': 'application/json',
                    'Content-Type': 'application/json',
                },
                mode: 'cors'
            })
        ]);

        console.log('📡 Response status codes:', {
            rides: ridesResponse.status,
            visited: visitedResponse.status,
            transfers: transfersResponse.status,
            lines: linesResponse.status
        });

        if (!ridesResponse.ok || !visitedResponse.ok || !transfersResponse.ok || !linesResponse.ok) {
            const errors = [];
            if (!ridesResponse.ok) errors.push(`Rides: ${ridesResponse.status} ${ridesResponse.statusText}`);
            if (!visitedResponse.ok) errors.push(`Visited: ${visitedResponse.status} ${visitedResponse.statusText}`);
            if (!transfersResponse.ok) errors.push(`Transfers: ${transfersResponse.status} ${transfersResponse.statusText}`);
            if (!linesResponse.ok) errors.push(`Lines: ${linesResponse.status} ${linesResponse.statusText}`);
            throw new Error('API Errors: ' + errors.join(', '));
        }

        const rides = await ridesResponse.json();
        const visitedStops = await visitedResponse.json();
        const transferStops = await transfersResponse.json();
        const popularLines = await linesResponse.json();

        console.log('✅ Data loaded successfully:', {
            rides: rides.rides ? rides.rides.length : 'N/A',
            visitedStops: visitedStops.length,
            transferStops: transferStops.length,
            popularLines: popularLines.length
        });

        // Extract rides array from API response
        const ridesArray = rides.rides || [];

        // Ensure all canvas elements exist before updating charts
        ensureCanvasElements();

        // Update summary stats with filtered data
        updateSummaryStats(ridesArray, visitedStops, transferStops, popularLines);

        // Update charts immediately - no delay needed since we're ensuring elements exist
        console.log('🎨 Starting chart updates...');
        updateVisitedStopsChart(visitedStops, ridesArray);
        updateTransferStopsChart(transferStops, ridesArray);
        updatePopularLinesChart(popularLines);
        updateBoroughChart(ridesArray);
        updateRidesOverTimeChart(ridesArray);
        console.log('🎨 Chart updates completed');

    } catch (error) {
        console.error('❌ Error loading data:', error);
        console.error('❌ Error details:', {
            name: error.name,
            message: error.message,
            stack: error.stack
        });

        // Check if this is a CORS/network error
        if (error.name === 'TypeError' && error.message.includes('NetworkError')) {
            showError(`Network error: Cannot connect to backend. Check CORS and URL configuration. Backend URL: ${backendUrl}`);
        } else {
            showError(`Failed to load statistics data: ${error.message}`);
        }
    }
}

function showLoading() {
    document.querySelectorAll('.chart-wrapper').forEach(wrapper => {
        wrapper.innerHTML = '<div class="loading">Loading data...</div>';
    });
}

function showError(message) {
    document.querySelectorAll('.chart-wrapper').forEach(wrapper => {
        wrapper.innerHTML = `<div class="error"><strong>Error:</strong><br>${message}</div>`;
    });
}

// Ensure all canvas elements exist and are properly initialized
function ensureCanvasElements() {
    const canvasIds = ['visitedStopsChart', 'transferStopsChart', 'popularLinesChart', 'boroughChart', 'ridesOverTimeChart'];

    canvasIds.forEach(id => {
        let canvas = document.getElementById(id);
        if (!canvas) {
            console.log(`🔧 Creating missing canvas element: ${id}`);
            // Find the wrapper for this chart
            const allWrappers = document.querySelectorAll('.chart-wrapper');
            const chartTitles = ['Most Visited Stops', 'Most Transferred At Stops', 'Most Popular Lines', 'Most Visited Boroughs', 'Rides Over Time'];
            const chartIndex = canvasIds.indexOf(id);

            if (chartIndex >= 0 && allWrappers[chartIndex]) {
                allWrappers[chartIndex].innerHTML = `<canvas id="${id}"></canvas>`;
            }
        } else {
            // Clear any loading/error content from wrapper but keep canvas
            const wrapper = canvas.closest('.chart-wrapper');
            if (wrapper) {
                const loadingOrError = wrapper.querySelector('.loading, .error, .no-data');
                if (loadingOrError) {
                    wrapper.innerHTML = `<canvas id="${id}"></canvas>`;
                }
            }
        }
    });

    console.log('🔧 Canvas elements verification completed');
}

// Update summary statistics
function updateSummaryStats(rides, visitedStops, transferStops, popularLines) {
    console.log('📊 Updating summary stats with:', {
        rides: rides.length,
        visitedStops: visitedStops.length,
        transferStops: transferStops.length,
        popularLines: popularLines.length
    });

    const totalRides = rides.length;

    // Calculate unique stops from the filtered rides data
    const allStops = new Set();
    rides.forEach(ride => {
        if (ride.board_stop) allStops.add(ride.board_stop);
        if (ride.depart_stop) allStops.add(ride.depart_stop);
    });
    const uniqueStops = allStops.size;

    // Calculate unique lines from the filtered rides data
    const uniqueLines = new Set(rides.map(r => r.line).filter(line => line)).size;

    // Calculate transfers from the filtered rides data
    const transfers = rides.filter(r => r.transferred === true || r.transferred === 1).length;
    const transferRate = totalRides > 0 ? Math.round((transfers / totalRides) * 100) : 0;

    // Calculate favorites
    const favoriteLine = popularLines.length > 0 ? popularLines[0].line : '-';

    // Calculate favorite borough
    const boroughCounts = {};
    rides.forEach(ride => {
        [ride.board_stop, ride.depart_stop].forEach(stop => {
            if (stop) {
                const borough = getStationBorough(stop);
                boroughCounts[borough] = (boroughCounts[borough] || 0) + 1;
            }
        });
    });

    const sortedBoroughs = Object.entries(boroughCounts)
        .filter(([borough]) => borough !== 'Unknown')
        .sort(([, a], [, b]) => b - a);
    const favoriteBorough = sortedBoroughs.length > 0 ? sortedBoroughs[0][0] : '-';

    // Update display with animation
    animateValue('totalRides', parseInt(document.getElementById('totalRides').textContent) || 0, totalRides);
    animateValue('uniqueStops', parseInt(document.getElementById('uniqueStops').textContent) || 0, uniqueStops);
    animateValue('uniqueLines', parseInt(document.getElementById('uniqueLines').textContent) || 0, uniqueLines);
    animateValue('transferRate', parseInt(document.getElementById('transferRate').textContent) || 0, transferRate, '%');

    // Update favorites (no animation for text)
    document.getElementById('favoriteLine').textContent = favoriteLine;
    document.getElementById('favoriteBorough').textContent = favoriteBorough;

    console.log('📊 Summary stats updated:', {
        totalRides,
        uniqueStops,
        uniqueLines,
        transferRate: `${transferRate}%`,
        favoriteLine,
        favoriteBorough
    });
}

// Animate value changes in summary stats
function animateValue(elementId, start, end, suffix = '') {
    const element = document.getElementById(elementId);
    if (!element) return;

    const duration = 500; // 500ms animation
    const startTime = performance.now();

    function animate(currentTime) {
        const elapsed = currentTime - startTime;
        const progress = Math.min(elapsed / duration, 1);

        const current = Math.round(start + (end - start) * progress);
        element.textContent = current.toLocaleString() + suffix;

        if (progress < 1) {
            requestAnimationFrame(animate);
        }
    }

    requestAnimationFrame(animate);
}

// Update visited stops chart
function updateVisitedStopsChart(data, rides) {
    const chartElement = document.getElementById('visitedStopsChart');
    if (!chartElement) {
        console.error('❌ visitedStopsChart element not found after ensureCanvasElements');
        return;
    }

    console.log('🎯 Updating visited stops chart with', data.length, 'items');

    // Destroy existing chart
    if (charts.visitedStops) {
        charts.visitedStops.destroy();
        delete charts.visitedStops;
    }

    if (!data || data.length === 0) {
        const chartWrapper = chartElement.closest('.chart-wrapper');
        if (chartWrapper) {
            chartWrapper.innerHTML = '<div class="no-data">No visited stops data for this time period</div>';
        }
        return;
    }

    // Consolidate stops and get colors
    const consolidatedData = consolidateStopsByLines(data, rides);
    const top10Data = consolidatedData.slice(0, 10);

    // Log for debugging
    console.log('📊 Chart data:', {
        original: data.length,
        consolidated: consolidatedData.length,
        top10: top10Data.length
    });

    const ctx = chartElement.getContext('2d');
    if (!ctx) {
        console.error('❌ Could not get canvas context for visitedStopsChart');
        return;
    }

    charts.visitedStops = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: top10Data.map(item => shortenStationName(item.stop_name)),
            datasets: [{
                label: 'Visits',
                data: top10Data.map(item => item.count),
                backgroundColor: top10Data.map(item => item.color),
                borderColor: top10Data.map(item => item.color),
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    callbacks: {
                        title: function (tooltipItems) {
                            const index = tooltipItems[0].dataIndex;
                            const item = top10Data[index];
                            return item.stop_name; // Use full name in tooltip
                        },
                        afterTitle: function (tooltipItems) {
                            const index = tooltipItems[0].dataIndex;
                            const item = top10Data[index];
                            if (item.lines && item.lines.length > 0) {
                                const lineDisplay = item.lines.join('/');
                                return `Lines: ${lineDisplay}`;
                            }
                            return '';
                        },
                        label: function (context) {
                            return `Visits: ${context.parsed.y}`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        color: '#e0e0e0',
                        stepSize: 1,
                        callback: function (value) {
                            return Number.isInteger(value) ? value : '';
                        }
                    },
                    grid: {
                        color: '#333'
                    }
                },
                x: {
                    ticks: {
                        color: '#e0e0e0',
                        maxRotation: 45
                    },
                    grid: {
                        color: '#333'
                    }
                }
            }
        }
    });

    console.log('✅ Visited stops chart created successfully');
}

// Update transfer stops chart
function updateTransferStopsChart(data, rides) {
    const chartElement = document.getElementById('transferStopsChart');
    if (!chartElement) {
        console.error('❌ transferStopsChart element not found after ensureCanvasElements');
        return;
    }

    console.log('🎯 Updating transfer stops chart with', data.length, 'items');

    // Destroy existing chart
    if (charts.transferStops) {
        charts.transferStops.destroy();
        delete charts.transferStops;
    }

    if (!data || data.length === 0) {
        const chartWrapper = chartElement.closest('.chart-wrapper');
        if (chartWrapper) {
            chartWrapper.innerHTML = '<div class="no-data">No transfer data for this time period</div>';
        }
        return;
    }

    // Consolidate stops and get colors
    const consolidatedData = consolidateStopsByLines(data, rides);
    const top10Data = consolidatedData.slice(0, 10);

    // Log for debugging
    console.log('📊 Transfer chart data:', {
        original: data.length,
        consolidated: consolidatedData.length,
        top10: top10Data.length
    });

    const ctx = chartElement.getContext('2d');
    if (!ctx) {
        console.error('❌ Could not get canvas context for transferStopsChart');
        return;
    }

    charts.transferStops = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: top10Data.map(item => shortenStationName(item.stop_name)),
            datasets: [{
                label: 'Transfers',
                data: top10Data.map(item => item.count),
                backgroundColor: top10Data.map(item => item.color),
                borderColor: top10Data.map(item => item.color),
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                },
                tooltip: {
                    callbacks: {
                        title: function (tooltipItems) {
                            const index = tooltipItems[0].dataIndex;
                            const item = top10Data[index];
                            return item.stop_name; // Use full name in tooltip
                        },
                        afterTitle: function (tooltipItems) {
                            const index = tooltipItems[0].dataIndex;
                            const item = top10Data[index];
                            if (item.lines && item.lines.length > 0) {
                                const lineDisplay = item.lines.join('/');
                                return `Lines: ${lineDisplay}`;
                            }
                            return '';
                        },
                        label: function (context) {
                            return `Transfers: ${context.parsed.y}`;
                        }
                    }
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        color: '#e0e0e0',
                        stepSize: 1,
                        callback: function (value) {
                            return Number.isInteger(value) ? value : '';
                        }
                    },
                    grid: {
                        color: '#333'
                    }
                },
                x: {
                    ticks: {
                        color: '#e0e0e0',
                        maxRotation: 45
                    },
                    grid: {
                        color: '#333'
                    }
                }
            }
        }
    });

    console.log('✅ Transfer stops chart created successfully');
}

// Update borough chart
function updateBoroughChart(rides) {
    const chartElement = document.getElementById('boroughChart');
    if (!chartElement) {
        console.error('❌ boroughChart element not found after ensureCanvasElements');
        return;
    }

    console.log('🗽 Updating borough chart with', rides.length, 'rides');

    // Destroy existing chart
    if (charts.boroughChart) {
        charts.boroughChart.destroy();
        delete charts.boroughChart;
    }

    if (!rides || rides.length === 0) {
        const chartWrapper = chartElement.closest('.chart-wrapper');
        if (chartWrapper) {
            chartWrapper.innerHTML = '<div class="no-data">No ride data for this time period</div>';
        }
        return;
    }

    // Calculate borough visit counts
    const boroughCounts = {};
    rides.forEach(ride => {
        [ride.board_stop, ride.depart_stop].forEach(stop => {
            if (stop) {
                const borough = getStationBorough(stop);
                if (borough !== 'Unknown') {
                    boroughCounts[borough] = (boroughCounts[borough] || 0) + 1;
                }
            }
        });
    });

    const boroughData = Object.entries(boroughCounts)
        .sort(([, a], [, b]) => b - a)
        .map(([borough, count]) => ({
            borough,
            count,
            color: boroughColors[borough] || boroughColors['Unknown']
        }));

    if (boroughData.length === 0) {
        const chartWrapper = chartElement.closest('.chart-wrapper');
        if (chartWrapper) {
            chartWrapper.innerHTML = '<div class="no-data">No borough data for this time period</div>';
        }
        return;
    }

    const ctx = chartElement.getContext('2d');
    if (!ctx) {
        console.error('❌ Could not get canvas context for boroughChart');
        return;
    }

    charts.boroughChart = new Chart(ctx, {
        type: 'pie',
        data: {
            labels: boroughData.map(item => item.borough),
            datasets: [{
                data: boroughData.map(item => item.count),
                backgroundColor: boroughData.map(item => item.color),
                borderColor: '#333',
                borderWidth: 2
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    position: 'bottom',
                    labels: {
                        color: '#e0e0e0',
                        padding: 20,
                        usePointStyle: true
                    }
                },
                tooltip: {
                    callbacks: {
                        label: function (context) {
                            const total = context.dataset.data.reduce((a, b) => a + b, 0);
                            const percentage = ((context.parsed / total) * 100).toFixed(1);
                            return `${context.label}: ${context.parsed} visits (${percentage}%)`;
                        }
                    }
                }
            }
        }
    });

    console.log('✅ Borough chart created successfully');
}

// Update popular lines chart
function updatePopularLinesChart(data) {
    const chartElement = document.getElementById('popularLinesChart');
    if (!chartElement) {
        console.error('❌ popularLinesChart element not found after ensureCanvasElements');
        return;
    }

    console.log('🎯 Updating popular lines chart with', data.length, 'items');

    // Destroy existing chart
    if (charts.popularLines) {
        charts.popularLines.destroy();
        delete charts.popularLines;
    }

    if (!data || data.length === 0) {
        const chartWrapper = chartElement.closest('.chart-wrapper');
        if (chartWrapper) {
            chartWrapper.innerHTML = '<div class="no-data">No line data for this time period</div>';
        }
        return;
    }

    // Process line data to add colors
    const processedData = processLineData(data);
    const top10Data = processedData.slice(0, 10);

    const ctx = chartElement.getContext('2d');
    if (!ctx) {
        console.error('❌ Could not get canvas context for popularLinesChart');
        return;
    }

    charts.popularLines = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: top10Data.map(item => item.line || 'Unknown'),
            datasets: [{
                label: 'Rides',
                data: top10Data.map(item => item.ride_count || 0),
                backgroundColor: top10Data.map(item => item.color),
                borderColor: top10Data.map(item => item.color),
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        color: '#e0e0e0',
                        stepSize: 1,
                        callback: function (value) {
                            return Number.isInteger(value) ? value : '';
                        }
                    },
                    grid: {
                        color: '#333'
                    }
                },
                x: {
                    ticks: {
                        color: '#e0e0e0'
                    },
                    grid: {
                        color: '#333'
                    }
                }
            }
        }
    });

    console.log('✅ Popular lines chart created successfully');
}

// Update rides over time chart
function updateRidesOverTimeChart(rides) {
    const chartElement = document.getElementById('ridesOverTimeChart');
    if (!chartElement) {
        console.error('❌ ridesOverTimeChart element not found after ensureCanvasElements');
        return;
    }

    console.log('🎯 Updating rides over time chart with', rides.length, 'rides for filter:', currentFilter);

    // Don't render chart for single day
    if (currentFilter === 'day') {
        const chartWrapper = chartElement.closest('.chart-wrapper');
        if (chartWrapper) {
            chartWrapper.innerHTML = '<div class="no-data">Chart not available for single day view</div>';
        }

        // Update chart title
        const titleElement = document.getElementById('ridesOverTimeTitle');
        if (titleElement) {
            titleElement.textContent = 'Rides Over Time';
        }
        return;
    }

    // Destroy existing chart
    if (charts.ridesOverTime) {
        charts.ridesOverTime.destroy();
        delete charts.ridesOverTime;
    }

    if (!rides || rides.length === 0) {
        const chartWrapper = chartElement.closest('.chart-wrapper');
        if (chartWrapper) {
            chartWrapper.innerHTML = '<div class="no-data">No ride data for this time period</div>';
        }
        return;
    }

    const ctx = chartElement.getContext('2d');
    if (!ctx) {
        console.error('❌ Could not get canvas context for ridesOverTimeChart');
        return;
    }

    let groupedData = {};
    let chartTitle = 'Rides Over Time';
    let labelFormat = '';

    // Group rides based on current filter
    if (currentFilter === 'all' || currentFilter === 'custom') {
        // Show monthly data with year
        chartTitle = 'Monthly Rides';
        rides.forEach(ride => {
            const date = new Date(ride.date || ride.created_at);
            const monthKey = date.toLocaleDateString('en-US', {
                month: 'short',
                year: '2-digit',
                timeZone: 'America/New_York'
            });
            groupedData[monthKey] = (groupedData[monthKey] || 0) + 1;
        });
    } else if (currentFilter === 'month') {
        // Show weekly data (first date of week starting Monday)
        chartTitle = 'Weekly Rides';
        rides.forEach(ride => {
            const date = new Date(ride.date || ride.created_at);
            // Get Monday of the week
            const dayOfWeek = date.getDay();
            const monday = new Date(date);
            monday.setDate(date.getDate() - (dayOfWeek === 0 ? 6 : dayOfWeek - 1));

            const weekKey = monday.toLocaleDateString('en-US', {
                month: 'numeric',
                day: 'numeric',
                timeZone: 'America/New_York'
            });
            groupedData[weekKey] = (groupedData[weekKey] || 0) + 1;
        });
    } else if (currentFilter === 'week') {
        // Show daily data (day of week)
        chartTitle = 'Daily Rides';
        const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
        rides.forEach(ride => {
            const date = new Date(ride.date || ride.created_at);
            const dayKey = dayNames[date.getDay()];
            groupedData[dayKey] = (groupedData[dayKey] || 0) + 1;
        });

        // Ensure all days are represented
        dayNames.forEach(day => {
            if (!(day in groupedData)) {
                groupedData[day] = 0;
            }
        });
    } else if (currentFilter === 'year') {
        // Show monthly data without year
        chartTitle = 'Monthly Rides This Year';
        const monthNames = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        rides.forEach(ride => {
            const date = new Date(ride.date || ride.created_at);
            const monthKey = monthNames[date.getMonth()];
            groupedData[monthKey] = (groupedData[monthKey] || 0) + 1;
        });

        // Ensure all months are represented
        monthNames.forEach(month => {
            if (!(month in groupedData)) {
                groupedData[month] = 0;
            }
        });
    }

    // Update chart title
    const titleElement = document.getElementById('ridesOverTimeTitle');
    if (titleElement) {
        titleElement.textContent = chartTitle;
    }

    // Sort data appropriately
    let labels, values;
    if (currentFilter === 'week') {
        // Sort by day of week
        const dayOrder = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
        labels = dayOrder;
        values = dayOrder.map(day => groupedData[day] || 0);
    } else if (currentFilter === 'year') {
        // Sort by month order
        const monthOrder = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
        labels = monthOrder;
        values = monthOrder.map(month => groupedData[month] || 0);
    } else {
        // Sort chronologically for other filters
        labels = Object.keys(groupedData).sort((a, b) => {
            if (currentFilter === 'month') {
                // Parse MM/DD format for weekly view
                const [monthA, dayA] = a.split('/').map(Number);
                const [monthB, dayB] = b.split('/').map(Number);
                const dateA = new Date(new Date().getFullYear(), monthA - 1, dayA);
                const dateB = new Date(new Date().getFullYear(), monthB - 1, dayB);
                return dateA - dateB;
            } else {
                // Parse "MMM YY" format for all-time view
                const parseDate = (str) => {
                    const [month, year] = str.split(' ');
                    const monthIndex = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'].indexOf(month);
                    return new Date(2000 + parseInt(year), monthIndex);
                };
                return parseDate(a) - parseDate(b);
            }
        });
        values = labels.map(label => groupedData[label]);
    }

    charts.ridesOverTime = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: labels,
            datasets: [{
                label: 'Rides',
                data: values,
                backgroundColor: colors.primary,
                borderColor: colors.primary,
                borderWidth: 1
            }]
        },
        options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
                legend: {
                    display: false
                }
            },
            scales: {
                y: {
                    beginAtZero: true,
                    ticks: {
                        color: '#e0e0e0',
                        stepSize: 1,
                        callback: function (value) {
                            return Number.isInteger(value) ? value : '';
                        }
                    },
                    grid: {
                        color: '#333'
                    }
                },
                x: {
                    ticks: {
                        color: '#e0e0e0',
                        maxRotation: 45
                    },
                    grid: {
                        color: '#333'
                    }
                }
            }
        }
    });

    console.log('✅ Rides over time chart created successfully');
}