// This is pulled more or less directly from the coropleth tutorial on the 
// leaflet website plus help from GPT

// Constants are defined in the dynamic templates


const buildMap = () => {

    // SETUP
    // Initialize map object
    const map = L.map('map');
    
    // Basemap layer (currently the grey-back pallet)
    L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png', {
        attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
        subdomains: 'abcd',
        maxZoom: 19
    }).addTo(map);


    // Define the legend control
    const legend = L.control({ position: 'bottomright' });

    legend.onAdd = function (map) {
        const div = L.DomUtil.create('div', 'info legend');
        return div;
    };
    legend.addTo(map);

    // UPDATE ON DATA
    fetch({{ geojsonurl|tojson }})
    .then(response => response.json())
    .then(mapUpdate)
    .catch(error => {
        console.error(
            'Error loading GeoJSON data at {{ geojsonurl|tojson }}:', 
            error
        );
    });
};


const mapUpdate = (geojsonData) => {
    // Verify that geojsonData is loaded
    if (
        !geojsonData 
        || !geojsonData.features 
        || geojsonData.features.length === 0
    ) {
        console.error(
            'GeoJSON data from {{ geojsonurl|tojson }} is empty or invalid.'
        );
        return;
    }

    // Extract properties from the first feature to get field names
    const properties = geojsonData.features[0].properties;

    // Filter numeric and boolean fields
    const fields = [];
    for (const key in properties) {
        const value = properties[key];
        if (
            (
                typeof value === 'number' 
                || typeof value === 'boolean'
            ) && (
                !key.endsWith('_moe')
            )
        ) {
            fields.push(key);
        }
    }

    // Check if any numeric or boolean fields are found
    if (fields.length === 0) {
        console.error(
            'No numeric or boolean fields found in GeoJSON properties.'
        );
        return;
    }

    // Populate the dropdown menu
    const fieldSelect = document.getElementById('field-select');
    fields.forEach(field => {
        const option = document.createElement('option');
        option.value = field;
        option.text = field;
        fieldSelect.appendChild(option);
    });

    let geojsonLayer;
    let validValues = []; 
    // Declare validValues outside the function to 
    // access it in updateLegend

    labeler = labelerFactory(fields);

    // Function to update the choropleth map based on the selected field
    function updateChoropleth(field) {
        if (geojsonLayer) {
            map.removeLayer(geojsonLayer);
        }

        // Extract values for the selected field
        const values = geojsonData.features.map(
            feature => feature.properties[field]
        );

        // Handle cases where values may be undefined
        validValues = values.filter(v => v !== undefined && v !== null);

        if (validValues.length === 0) {
            console.error(`Field "${field}" has no valid data.`);
            return;
        }

        const min = Math.min(...validValues);
        const max = Math.max(...validValues);

        // Define a color scale
        let getColor;
        if (typeof validValues[0] === 'boolean') {
            getColor = d => d ? '#1f78b4' : '#e31a1c';
        } else {
            getColor = d => {
                const ratio = (d - min) / (max - min);
                return `hsl(${(1 - ratio) * 240}, 100%, 50%)`; // Blue to red scale
            };
        }

        function style(feature) {
            const value = feature.properties[field];
            return {
                fillColor: getColor(value),
                weight: 1,
                opacity: 1,
                color: 'white',
                fillOpacity: 0.7
            };
        }


        geojsonLayer = L.geoJson(geojsonData, {
            style: style,
            onEachFeature: labeler // Adds the pop up for all fields
        }).addTo(map);

        // Adjust the map view to fit the GeoJSON layer with padding
        map.fitBounds(geojsonLayer.getBounds(), {
            padding: [20, 20] // Adjust the padding as needed
        });

        // Update the legend
        updateLegend(field, min, max, getColor);
    }

    // Function to update the legend
    function updateLegend(field, min, max, getColor) {
        const legendDiv = document.querySelector('.legend');
        if (!legendDiv) return;

        // Clear existing content
        legendDiv.innerHTML = `<h4>${field}</h4>`;

        if (typeof min === 'undefined' || typeof max === 'undefined') {
            legendDiv.innerHTML += 'No data available';
            return;
        }

        // Create legend content based on data type
        if (typeof validValues[0] === 'boolean') {
            // For boolean fields
            const categories = [true, false];
            categories.forEach(value => {
                legendDiv.innerHTML +=
                    `<i style="background:${getColor(value)}"></i> ${value}<br>`;
            });
        } else {
            // For numeric fields
            const grades = 5; // Number of legend intervals
            const step = (max - min) / grades;
            const labels = [];
            for (let i = 0; i <= grades; i++) {
                const value = min + step * i;
                labels.push(value.toFixed(2));
            }

            // Generate legend labels
            for (let i = 0; i < labels.length - 1; i++) {
                const from = labels[i];
                const to = labels[i + 1];
                legendDiv.innerHTML +=
                    `<i style="background:${getColor(+from)}"></i> ${from} &ndash; ${to}<br>`;
            }
            legendDiv.innerHTML +=
                `<i style="background:${getColor(labels.length - 1)}"></i> over ${labels[labels.length -1]}<br>`;
        }
    }

    // Initial rendering with the first field
    updateChoropleth(fields[0]);

    // Update the map when a new field is selected
    fieldSelect.addEventListener('change', e => {
        updateChoropleth(e.target.value);
    });
}

function labelerFactory(valFields) {
    const labeler = (feature, layer) => {
        layer.bindPopup(
            valFields.map(
                field => `<strong>${field}:</strong> ${feature.properties[field]}`
            ).join("\n")
        );
    };
    return labeler;
}


function getColor(d) {
    return d > 1000 ? '#800026' :
           d > 500  ? '#BD0026' :
           d > 200  ? '#E31A1C' :
           d > 100  ? '#FC4E2A' :
           d > 50   ? '#FD8D3C' :
           d > 20   ? '#FEB24C' :
           d > 10   ? '#FED976' :
                      '#FFEDA0';
}


function style(feature) {
    return {
        fillColor: getColor(feature.properties.density),
        weight: 2,
        opacity: 1,
        color: 'white',
        fillOpacity: 0.7
    };
}
