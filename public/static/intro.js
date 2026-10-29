// The opening (templates/_intro.html): falling binary, TRIPWIRE typed out, ARCHIVE & PRESERVE,
// and the purple rabbit hopping in like Pixar's lamp: crouch, stretch, land, squash, look at you.
// "Trip the wires" floods the screen with binary, plays Purple's welcome (if there is a
// recording at public/static/audio/welcome.mp3; without one it stays quiet), then fades away to show the computer.
(function () {
  var intro = document.getElementById("intro");
  if (!intro) return;
  var reduced = (window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches) ||
                document.documentElement.classList.contains("a11y-calm");   // calm mode: nothing moves
  var canvas = intro.querySelector(".rain"), ctx = canvas.getContext("2d");
  var bunny = intro.querySelector(".bunny"), trip = document.getElementById("trip");
  var skip = document.getElementById("intro-skip");
  var ending = false;

  function wait(ms) { return new Promise(function (resolve) { setTimeout(resolve, ms); }); }
  function colour(name) { return getComputedStyle(intro).getPropertyValue(name).trim(); }

  // ---- the binary rain ------------------------------------------------------
  var size, columns, drops, active, speed = 1, density = 0.45, timer;
  function setUp() {
    var ratio = window.devicePixelRatio || 1;
    canvas.width = innerWidth * ratio; canvas.height = innerHeight * ratio;
    canvas.style.width = innerWidth + "px"; canvas.style.height = innerHeight + "px";
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    size = innerWidth < 640 ? 15 : 19;
    columns = Math.ceil(innerWidth / size);
    drops = []; active = [];
    for (var i = 0; i < columns; i++) {
      drops.push(-Math.random() * innerHeight / size);
      active.push(Math.random() < density);
    }
    ctx.fillStyle = colour("--intro-bg");
    ctx.fillRect(0, 0, innerWidth, innerHeight);
  }
  function frame() {
    ctx.fillStyle = colour("--rain-fade");          // last frames fade, leaving trails
    ctx.fillRect(0, 0, innerWidth, innerHeight);
    ctx.font = size + 'px "VT323", "IBM Plex Mono", monospace';
    var body = colour("--rain"), head = colour("--rain-head");
    for (var i = 0; i < columns; i++) {
      if (!active[i]) { if (Math.random() < density / 400) active[i] = true; continue; }
      var y = drops[i] * size;
      ctx.fillStyle = Math.random() < 0.12 ? head : body;
      ctx.fillText(Math.random() < 0.5 ? "0" : "1", i * size, y);
      drops[i] += speed;
      if (y > innerHeight && Math.random() > 0.96) {
        drops[i] = -Math.random() * 8;
        active[i] = Math.random() < density;
      }
    }
  }
  function staticRain() {                           // reduced motion: one still frame
    for (var n = 0; n < 40; n++) frame();
  }

  // ---- typing ---------------------------------------------------------------
  function type(el, perLetter) {
    var text = el.dataset.text;
    if (reduced) { el.textContent = text; return Promise.resolve(); }
    el.textContent = "";
    el.parentElement.classList.add("shown");
    return new Promise(function (resolve) {
      var i = 0;
      (function next() {
        el.textContent = text.slice(0, ++i);
        if (i >= text.length) resolve(); else setTimeout(next, perLetter);
      })();
    });
  }

  // ---- the rabbit's entrance -------------------------------------------------
  // It faces left, so it comes in from the right. Each hop: crouch (squash), take off
  // (stretch, leaning forward), land (squash), settle. Hops get smaller as it arrives.
  function hop(from, to, height, ms) {
    var mid = (from + to) / 2;
    return bunny.animate([
      { transform: "translate(" + from + "px, 0) scale(1, 1) rotate(0deg)", easing: "ease-in" },
      { transform: "translate(" + from + "px, 0) scale(1.12, .84) rotate(0deg)", offset: .2, easing: "ease-out" },
      { transform: "translate(" + mid + "px, " + -height + "px) scale(.9, 1.12) rotate(-9deg)", offset: .55, easing: "ease-in" },
      { transform: "translate(" + to + "px, 0) scale(1.14, .82) rotate(2deg)", offset: .84, easing: "ease-out" },
      { transform: "translate(" + to + "px, 0) scale(1, 1) rotate(0deg)" }
    ], { duration: ms, fill: "forwards" }).finished;
  }
  function lookAtYou(x) {                           // the Luxo moment: a curious head tilt
    function at(deg) { return { transform: "translate(" + x + "px, 0) rotate(" + deg + "deg)" }; }
    var tilt = [at(0), at(7), at(7), at(-3), at(0)];
    tilt[1].offset = .3; tilt[2].offset = .55; tilt[3].offset = .8;
    return bunny.animate(tilt, { duration: 900, easing: "ease-in-out", fill: "forwards" }).finished;
  }
  async function enter() {
    var start = innerWidth / 2 + bunny.offsetWidth * 1.2;   // just off the right edge
    var tall = bunny.offsetHeight;
    var stops = [1, .7, .44, .22, .08], heights = [.5, .44, .36, .26], times = [470, 450, 430, 390];
    bunny.style.transform = "translate(" + start + "px, 0)";
    bunny.classList.add("ready");
    for (var i = 0; i < 4; i++) {
      await hop(start * stops[i], start * stops[i + 1], tall * heights[i], times[i]);
      if (ending) return;
      await wait(i === 3 ? 260 : 60);
    }
    await lookAtYou(start * stops[4]);               // stops, and looks at you...
    if (ending) return;
    await wait(150);
    await hop(start * stops[4], 0, tall * .16, 360); // ...one last little hop into place
    intro.querySelector(".intro-tagline").animate([{ transform: "translateY(0)" }, { transform: "translateY(3px)" },
      { transform: "translateY(0)" }], { duration: 220, easing: "ease-out" });
  }

  // ---- the sequence ----------------------------------------------------------
  async function play() {
    if (reduced) {
      staticRain();
      intro.querySelectorAll(".typed").forEach(function (el) { el.parentElement.classList.add("shown"); });
      bunny.classList.add("ready");
      showButton();
      return;
    }
    timer = setInterval(frame, 55);
    await wait(500);
    await type(intro.querySelector(".intro-title .typed"), 150);
    await wait(350);
    await type(intro.querySelector(".intro-tagline .typed"), 55);
    await wait(300);
    if (ending) return;
    await enter();
    if (!ending) showButton();
  }
  function showButton() {
    intro.classList.add("landed");
    if (!ending) trip.focus({ preventScroll: true });
  }

  // A small sound when the wires are tripped (allowed now: the visitor clicked).
  function chirp() {
    try {
      var audio = new (window.AudioContext || window.webkitAudioContext)();
      var osc = audio.createOscillator(), gain = audio.createGain();
      osc.type = "square";
      osc.frequency.setValueAtTime(880, audio.currentTime);
      osc.frequency.exponentialRampToValueAtTime(110, audio.currentTime + .5);
      gain.gain.setValueAtTime(.05, audio.currentTime);
      gain.gain.exponentialRampToValueAtTime(.0001, audio.currentTime + .55);
      osc.connect(gain).connect(audio.destination);
      osc.start(); osc.stop(audio.currentTime + .6);
    } catch (e) { /* no sound: fine */ }
  }

  function bootComputer() {
    var boot = document.getElementById("boot");
    if (boot && window.Tripwire && window.Tripwire.type) window.Tripwire.type(boot, 14);
  }
  function finish() {
    intro.classList.add("leaving");
    document.documentElement.classList.remove("intro-lock");
    setTimeout(function () {
      clearInterval(timer); intro.remove();
      document.dispatchEvent(new Event("tripwire:intro-done"));   // e.g. the read-aloud question comes next
    }, 700);
  }

  async function tripTheWires() {
    if (ending) return;
    ending = true;
    if (intro.dataset.voice) new Audio(intro.dataset.voice).play().catch(function () {});
    chirp();
    intro.classList.add("tripped");                  // the screen goes black...
    if (reduced) { bootComputer(); finish(); return; }
    density = 1; speed = 1.8;                         // ...and binary rains across all of it
    active = active.map(function () { return true; });
    await wait(450);
    intro.classList.add("storm");
    await wait(1500);
    bootComputer();
    finish();
  }
  function skipIntro() {
    ending = true;
    bootComputer();
    finish();
  }

  trip.addEventListener("click", tripTheWires);
  skip.addEventListener("click", skipIntro);
  document.addEventListener("keydown", function (event) {
    if (!document.body.contains(intro)) return;
    if (event.key === "Escape") skipIntro();
  });
  addEventListener("resize", function () { if (!ending) setUp(); });

  function begin() {
    setUp();
    // Wait (briefly) for the pixel font, so the rain and the title start in the right letters.
    var fonts = document.fonts && document.fonts.load ? document.fonts.load('20px "VT323"') : Promise.resolve();
    Promise.race([fonts, wait(800)]).then(play);
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", begin); else begin();
})();
