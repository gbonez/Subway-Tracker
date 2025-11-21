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

// Get MTA color for a line
function getMTAColor(line) {
    return mtaLineColors[line] || '#808183'; // Default to gray if line not found
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

            // Update primary line if this station has higher usage
            if (stopLineUsage[stopName]) {
                for (const [line, usage] of Object.entries(stopLineUsage[stopName])) {
                    if (usage > maxUsage) {
                        consolidatedStops[consolidationKey].primary_line = line;
                        consolidatedStops[consolidationKey].color = getMTAColor(line);
                    }
                }
            }
        }
    });

    return Object.values(consolidatedStops).sort((a, b) => b.count - a.count);
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
            start.setDate(now.getDate() - 7);
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
    const canvasIds = ['visitedStopsChart', 'transferStopsChart', 'popularLinesChart', 'ridesOverTimeChart'];

    canvasIds.forEach(id => {
        let canvas = document.getElementById(id);
        if (!canvas) {
            console.log(`🔧 Creating missing canvas element: ${id}`);
            // Find the wrapper for this chart
            const allWrappers = document.querySelectorAll('.chart-wrapper');
            const chartTitles = ['Most Visited Stops', 'Most Transferred At Stops', 'Most Popular Lines', 'Rides Over Time'];
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

    // Update display with animation
    animateValue('totalRides', parseInt(document.getElementById('totalRides').textContent) || 0, totalRides);
    animateValue('uniqueStops', parseInt(document.getElementById('uniqueStops').textContent) || 0, uniqueStops);
    animateValue('uniqueLines', parseInt(document.getElementById('uniqueLines').textContent) || 0, uniqueLines);
    animateValue('transferRate', parseInt(document.getElementById('transferRate').textContent) || 0, transferRate, '%');

    console.log('📊 Summary stats updated:', {
        totalRides,
        uniqueStops,
        uniqueLines,
        transferRate: `${transferRate}%`
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

    const ctx = chartElement.getContext('2d');
    if (!ctx) {
        console.error('❌ Could not get canvas context for visitedStopsChart');
        return;
    }

    charts.visitedStops = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: top10Data.map(item => item.stop_name),
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
                            return item.stop_name;
                        },
                        afterTitle: function (tooltipItems) {
                            const index = tooltipItems[0].dataIndex;
                            const item = top10Data[index];
                            if (item.lines && item.lines.length > 0) {
                                const lineDisplay = item.lines.join('/');
                                const complexInfo = item.is_transfer_complex ? ' (Transfer Complex)' : '';
                                return `Lines: ${lineDisplay}${complexInfo}`;
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

    const ctx = chartElement.getContext('2d');
    if (!ctx) {
        console.error('❌ Could not get canvas context for transferStopsChart');
        return;
    }

    charts.transferStops = new Chart(ctx, {
        type: 'bar',
        data: {
            labels: top10Data.map(item => item.stop_name),
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
                            return item.stop_name;
                        },
                        afterTitle: function (tooltipItems) {
                            const index = tooltipItems[0].dataIndex;
                            const item = top10Data[index];
                            if (item.lines && item.lines.length > 0) {
                                const lineDisplay = item.lines.join('/');
                                const complexInfo = item.is_transfer_complex ? ' (Transfer Complex)' : '';
                                return `Lines: ${lineDisplay}${complexInfo}`;
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

    console.log('🎯 Updating rides over time chart with', rides.length, 'rides');

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

    // Group rides by date
    const ridesByDate = rides.reduce((acc, ride) => {
        const date = ride.date || ride.created_at?.split('T')[0] || 'Unknown';
        acc[date] = (acc[date] || 0) + 1;
        return acc;
    }, {});

    // Sort dates and prepare data
    const sortedDates = Object.keys(ridesByDate).sort();
    const rideCounts = sortedDates.map(date => ridesByDate[date]);

    charts.ridesOverTime = new Chart(ctx, {
        type: 'line',
        data: {
            labels: sortedDates,
            datasets: [{
                label: 'Rides per Day',
                data: rideCounts,
                backgroundColor: 'rgba(76, 175, 80, 0.2)',
                borderColor: colors.primary,
                borderWidth: 2,
                fill: true,
                tension: 0.4
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