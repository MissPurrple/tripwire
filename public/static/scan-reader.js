// Reading scanned PDFs (pictures of pages) inside the visitor's own browser.
// The document never leaves their device: only the text that's read is sent
// to Tripwire. Uses pdf.js to draw each page and Tesseract to read it; both are
// served by Tripwire itself and only loaded when a PDF is chosen.
window.Tripwire = window.Tripwire || {};
(function (T) {
  var BASE = "/static/ocr/";
  var loading = null;

  function loadScript(src) {
    return new Promise(function (resolve, reject) {
      var s = document.createElement("script");
      s.src = src; s.onload = resolve; s.onerror = function () { reject(new Error("Couldn't load " + src)); };
      document.head.appendChild(s);
    });
  }
  function loadTools() {
    loading = loading || Promise.all([loadScript(BASE + "pdf.min.js"), loadScript(BASE + "tesseract.min.js")]).then(function () {
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = BASE + "pdf.worker.min.js";
    });
    return loading;
  }

  // Does this PDF contain real text, or is it pictures of pages? Looks at the first few pages.
  async function hasText(pdf) {
    var pages = Math.min(pdf.numPages, 3), chars = 0;
    for (var i = 1; i <= pages; i++) {
      var content = await (await pdf.getPage(i)).getTextContent();
      chars += content.items.map(function (item) { return item.str; }).join("").replace(/\s/g, "").length;
    }
    return chars / pages > 80;
  }

  function newWorker(onProgress) {
    return window.Tesseract.createWorker("eng", 1, {
      workerPath: BASE + "worker.min.js", corePath: BASE + "core", langPath: BASE + "lang", gzip: true,
      logger: function (m) { if (m.status === "recognizing text") onProgress(m.progress); }
    });
  }

  // Returns {text, pages: 1} for a photo of a page (JPG, PNG, WebP), read on this device.
  T.readImage = async function (file, onProgress) {
    await loadTools();
    var worker = await newWorker(function (part) { onProgress(1, 1, part); });
    try {
      return { text: (await worker.recognize(file)).data.text, pages: 1 };
    } finally {
      await worker.terminate();
    }
  };

  // Returns {text, pages} for a PDF that already has text, read on this device (for locked
  // items: Tripwire can't read them, so their words are read here before they're locked).
  T.readPdfText = async function (file) {
    await loadTools();
    var pdf = await window.pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise, texts = [];
    for (var i = 1; i <= pdf.numPages; i++) {
      var content = await (await pdf.getPage(i)).getTextContent();
      texts.push(content.items.map(function (item) { return item.str + (item.hasEOL ? "\n" : " "); }).join(""));
    }
    return { text: texts.join("\n\n"), pages: pdf.numPages };
  };

  // Returns {text, pages} for a scanned PDF, or null if the PDF already has text
  // (then Tripwire reads it normally). onProgress(pageNumber, pageCount, fractionOfPage).
  T.readScannedPdf = async function (file, onProgress) {
    await loadTools();
    var pdf = await window.pdfjsLib.getDocument({ data: await file.arrayBuffer() }).promise;
    if (await hasText(pdf)) return null;
    var current = 0;
    var worker = await newWorker(function (part) { onProgress(current, pdf.numPages, part); });
    var texts = [];
    try {
      for (current = 1; current <= pdf.numPages; current++) {
        onProgress(current, pdf.numPages, 0);
        var page = await pdf.getPage(current);
        var scale = Math.min(2.5, 2000 / page.getViewport({ scale: 1 }).width);  // sharp enough to read, not huge
        var viewport = page.getViewport({ scale: scale });
        var canvas = document.createElement("canvas");
        canvas.width = viewport.width; canvas.height = viewport.height;
        await page.render({ canvasContext: canvas.getContext("2d"), viewport: viewport }).promise;
        var result = await worker.recognize(canvas);
        texts.push(result.data.text);
      }
    } finally {
      await worker.terminate();
    }
    return { text: texts.join("\n\n"), pages: pdf.numPages };
  };
})(window.Tripwire);
