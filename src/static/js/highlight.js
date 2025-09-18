let pdfDocument = null;
let textContent = null;
let viewport = null;

document.getElementById('pdf-file').addEventListener('change', (event) => {
    const file = event.target.files[0];
    if (file) {
        const fileReader = new FileReader();
        fileReader.onload = function() {
            const typedArray = new Uint8Array(this.result);
            loadAndProcessPDF(typedArray);
        };
        fileReader.readAsArrayBuffer(file);
    }
});

function loadAndProcessPDF(data) {
    console.log("Loading PDF file...");
    const loadingTask = pdfjsLib.getDocument(data);
    loadingTask.promise.then((pdf) => {
        pdfDocument = pdf;
        return pdf.getPage(1);
    }).then((page) => {
        const scale = 1.5;
        viewport = page.getViewport({ scale: 1 });

        const canvas = document.getElementById('pdf-canvas');
        const context = canvas.getContext('2d');
        canvas.height = viewport.height;
        canvas.width = viewport.width;

        const renderContext = {
            canvasContext: context,
            viewport: viewport
        };
        return page.render(renderContext).promise.then(() => {
            return page.getTextContent();
        });
    }).then((text) => {
        textContent = text;
        console.log("Extracted text content:", text.items.map(item => item.str).join(' '));  // Log extracted text
    });
}

function processTextWithNLP(text, searchTerm) {
    fetch('/process', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json'
        },
        body: JSON.stringify({ text: text, search_term: searchTerm })
    })
    .then(response => response.json())
    .then(data => {
        console.log("Search results:", data);  // Log search results
        highlightResults(data, textContent);
    });
}

function highlightResults(results, textContent) {
    const context = document.getElementById('pdf-canvas').getContext('2d');
    results.forEach(result => {
        const [text, type] = result;
        const searchText = text.trim().toLowerCase();
        console.log(`Normalized search text: "${searchText}"`);

        // Find the starting index of the search phrase in the full concatenated text
        let concatenatedText = textContent.items.map(item => item.str).join(' ').toLowerCase();
        let index = concatenatedText.indexOf(searchText);

        if (index !== -1) {
            let currentIndex = 0;
            let startIndex = -1;
            let endIndex = -1;

            // Identify the start and end indices for the highlight
            textContent.items.forEach((item, idx) => {
                const itemText = item.str.trim().toLowerCase();
                const itemLength = itemText.length;

                if (currentIndex <= index && currentIndex + itemLength > index) {
                    startIndex = idx;
                }
                if (currentIndex < index + searchText.length && currentIndex + itemLength >= index + searchText.length) {
                    endIndex = idx;
                }
                currentIndex += itemLength + 1; // +1 for space
            });

            if (startIndex !== -1 && endIndex !== -1) {
                let highlightX = 0;
                let highlightY = 0;
                let highlightWidth = 0;
                let highlightHeight = 0;

                for (let i = startIndex; i <= endIndex; i++) {
                    const item = textContent.items[i];
                    const transform = item.transform;
                    const x = transform[4];
                    const y = transform[5];
                    const width = context.measureText(item.str).width;
                    let height = Math.abs(transform[1]);  // Use transform height
                    if (height < 1) height = 10;  // Set default height if too small or zero

                    if (i === startIndex) {
                        highlightX = x;
                        highlightY = y;
                    }
                    highlightWidth += width;

                    // Adjust the height to the maximum height of the text items
                    if (height > highlightHeight) {
                        highlightHeight = height;
                    }
                }

                // Apply the highlight for the entire phrase
                context.fillStyle = 'yellow';
                context.globalAlpha = 0.3;
                const adjustedY = viewport.height - highlightY - highlightHeight;
                context.fillRect(highlightX, adjustedY, highlightWidth, highlightHeight);
                context.globalAlpha = 1.0;

                // Log for debugging
                console.log(`Highlighted phrase: ${searchText} at (x: ${highlightX}, y: ${adjustedY}, width: ${highlightWidth}, height: ${highlightHeight})`);
            }
        }
    });
}

function searchPDF() {
    const searchTerm = document.getElementById('search-term').value;
    const fullText = textContent.items.map(item => item.str).join(' ');
    console.log("Full extracted text:", fullText);  // Log full extracted text
    console.log("Search term:", searchTerm);  // Log search term
    processTextWithNLP(fullText, searchTerm);
}

document.addEventListener("DOMContentLoaded", function() {
    // No need to load a hardcoded PDF on load
});
