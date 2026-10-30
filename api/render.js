// Reading a page the way a browser does: opens it in a real (headless) Chromium, waits for its
// words to load, and sends back the text a person would see. Some pages, like the Artizen
// Playbook, load their words only after they open, so a plain download finds an empty page.
//
// Only Tripwire itself may call this (a key made from TRIPWIRE_SECRET), it never visits private
// network addresses (not even for things the page asks for), and it skips images, video and fonts.
// Tripwire's Python side (tripwire/render.py) decides when a page needs it.
const crypto = require("crypto");
const dns = require("dns").promises;
const net = require("net");

const MAX_TEXT = 2000000;
const LOAD_TIMEOUT_MS = 15000;

function expectedKey() {
  const secret = process.env.TRIPWIRE_SECRET;
  return secret ? crypto.createHash("sha256").update("tripwire-render-v1:" + secret).digest("hex") : null;
}

function isPrivate(address) {
  if (net.isIPv4(address)) {
    const [a, b] = address.split(".").map(Number);
    return a === 10 || a === 127 || a === 0 || (a === 169 && b === 254) || (a === 172 && b >= 16 && b <= 31) ||
           (a === 192 && b === 168) || (a === 100 && b >= 64 && b <= 127) || a >= 224;
  }
  const lower = address.toLowerCase();
  if (lower.startsWith("::ffff:")) return isPrivate(lower.slice(7));
  return lower === "::1" || lower === "::" || lower.startsWith("fc") || lower.startsWith("fd") || lower.startsWith("fe80");
}

const verdicts = new Map();  // hostname -> Promise<boolean>: is it safe to visit?
function safeHost(hostname) {
  if (process.env.RENDER_ALLOW_PRIVATE === "1") return Promise.resolve(true);   // local testing only
  if (!verdicts.has(hostname)) {
    verdicts.set(hostname, (net.isIP(hostname) ? Promise.resolve([{ address: hostname }]) : dns.lookup(hostname, { all: true }))
      .then(list => list.length > 0 && list.every(entry => !isPrivate(entry.address)))
      .catch(() => false));
  }
  return verdicts.get(hostname);
}

async function launch() {
  const puppeteer = require("puppeteer-core");
  if (process.env.CHROME_PATH) {   // a laptop or test machine with its own Chromium
    return puppeteer.launch({ executablePath: process.env.CHROME_PATH, headless: true, args: ["--no-sandbox"] });
  }
  // The small Chromium made for servers like Vercel's (a newer-style module, so it's imported, not required).
  const chromium = (await import("@sparticuz/chromium")).default;
  return puppeteer.launch({ args: chromium.args, executablePath: await chromium.executablePath(), headless: true });
}

module.exports = async (req, res) => {
  const key = expectedKey();
  if (!key) return res.status(503).json({ error: "Browser reading isn't set up (TRIPWIRE_SECRET is missing)." });
  const given = String(req.headers["x-tripwire-render"] || "");
  if (req.method !== "POST" || given.length !== key.length ||
      !crypto.timingSafeEqual(Buffer.from(given), Buffer.from(key))) {
    return res.status(403).json({ error: "Not allowed." });
  }
  let target;
  try {
    target = new URL(String((req.body || {}).url || ""));
    if (!["http:", "https:"].includes(target.protocol)) throw new Error();
  } catch (e) {
    return res.status(400).json({ error: "That isn't a web link." });
  }
  if (!(await safeHost(target.hostname))) return res.status(400).json({ error: "Tripwire doesn't visit private network addresses." });

  let browser;
  try {
    browser = await launch();
    const page = await browser.newPage();
    await page.setUserAgent("Mozilla/5.0 (compatible; Tripwire/0.1; +information preservation tool)");
    await page.setRequestInterception(true);
    page.on("request", async request => {
      try {
        const kind = request.resourceType();
        if (["image", "media", "font"].includes(kind)) return request.abort();
        const url = new URL(request.url());
        if (["data:", "blob:"].includes(url.protocol)) return request.continue();
        if (!["http:", "https:"].includes(url.protocol) || !(await safeHost(url.hostname))) return request.abort();
        return request.continue();
      } catch (e) {
        return request.abort();
      }
    });
    let status = null;
    try {
      const response = await page.goto(target.href, { waitUntil: "networkidle2", timeout: LOAD_TIMEOUT_MS });
      status = response ? response.status() : null;
    } catch (e) {
      if (e.name !== "TimeoutError") throw e;   // still loading: read whatever has arrived
    }
    await new Promise(resolve => setTimeout(resolve, 500));   // a moment for the last words to appear
    const page_ = await page.evaluate(() => ({ text: document.body ? document.body.innerText : "", title: document.title }));
    return res.status(200).json({ text: page_.text.slice(0, MAX_TEXT), title: page_.title, url: page.url(), status });
  } catch (e) {
    console.error("Tripwire browser reading failed:", e && e.message);   // for the site's logs, not the visitor
    return res.status(502).json({ error: "The browser couldn't open that page." });
  } finally {
    if (browser) await browser.close().catch(() => {});
  }
};
