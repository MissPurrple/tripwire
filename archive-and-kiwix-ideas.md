# Brainstorm: a local archive you keep yourself, and Kiwix (29 Oct 2026)

Purple's need: documents like Lesotho government gazettes and memos (often scanned PDFs) live on government
websites that are hard to navigate, may be run by outsiders, and can lose things. A local person who cares
should be able to keep their own copy, independent of the site, and notice when the original vanishes.

## Idea: "My shelf": a personal archive that leaves with you
- Add a document (upload a scanned PDF, or paste a link to one). Tripwire keeps the file, reads it on your
  device (already built), and makes it searchable.
- If it came from a link, Tripwire keeps watching the link (already built for PDF links): you're told if the
  original is replaced or removed, and you still have your copy.
- "Take my shelf with me": download everything as ONE file you own: your documents, their text, and the
  record of changes. Nothing depends on Tripwire still existing.

## Where Kiwix comes in
Kiwix (kiwix.org) opens ".zim" files: whole libraries in one file that work with NO internet, on phones,
laptops, Raspberry Pis, even shared over a local network in a school or village. That's exactly the
"lives somewhere local" answer.
1. Export my shelf as a ZIM: the download in the idea above, in Kiwix's format. Open it in the free Kiwix app,
   fully offline, searchable. Copy it to a USB stick or another phone; share it person to person, not through
   a government site or a big platform. (Tools exist to build ZIM files from a folder of web pages; Tripwire
   would generate those pages.)
2. Compare against a Kiwix copy: Kiwix already publishes dated offline copies of Wikipedia. Tripwire could
   compare today's article with the copy from a past Kiwix snapshot, a trusted "before" that nobody can edit.
3. Community shelves: a group (a library, a church, a school) builds a shelf together, exports it as a ZIM,
   and runs it on a Kiwix "hotspot" (a cheap box that serves it over Wi-Fi to nearby phones). Local, offline,
   owned by the community.

## Why it fits Tripwire
- Preservation without handing knowledge to platforms: the community holds the copy.
- Works where internet is expensive or unreliable.
- Scanned PDFs are hard to alter invisibly (Purple's point): keeping the original scan alongside its text
  preserves that evidence.

## Possible build order
1. "My shelf" page (add, read, search, watch the source link)
2. "Download my shelf" as a plain folder/ZIP with an index page
3. The same as a ZIM file for Kiwix
4. Compare with a Kiwix Wikipedia snapshot
