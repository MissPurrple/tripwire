Tools Tripwire uses to read scanned PDFs inside the visitor's own browser.
Served by Tripwire itself, so documents and visitors are never sent to an outside service.

- tesseract.js 5.1.1 and tesseract.js-core 5.1.1 (Apache-2.0): reads text from images
- lang/eng.traineddata.gz from @tesseract.js-data/eng 1.0.0 (4.0.0_best_int, Apache-2.0): English (Latin alphabet) reading data
- pdf.js / pdfjs-dist 3.11.174 (Apache-2.0, Mozilla): opens PDFs and draws their pages
