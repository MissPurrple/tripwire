// The vault on the website: keeps the unlocked key on this device while it's in use, and
// shows locked titles once it's unlocked. The locking itself is in vault-core.js.
//
// The unlocked key is kept in this browser's own storage (IndexedDB) as a key that can be used
// but never read out, for at most 30 minutes after it was last used. It's forgotten when the
// person logs out (or on any page where nobody with a vault is logged in).
(function () {
  var T = window.Tripwire = window.Tripwire || {};
  var IDLE_MINUTES = 30, STORE = "keys";

  function database() {
    return new Promise(function (resolve, reject) {
      var request = indexedDB.open("tripwire-vault", 1);
      request.onupgradeneeded = function () { request.result.createObjectStore(STORE); };
      request.onsuccess = function () { resolve(request.result); };
      request.onerror = function () { reject(request.error); };
    });
  }
  function run(mode, work) {
    return database().then(function (db) {
      return new Promise(function (resolve, reject) {
        var tx = db.transaction(STORE, mode), result = work(tx.objectStore(STORE));
        tx.oncomplete = function () { resolve(result && result.result); };
        tx.onerror = function () { reject(tx.error); };
      });
    });
  }
  function forget() { return run("readwrite", function (s) { return s.clear(); }).catch(function () {}); }

  function user() { return document.body ? document.body.dataset.vaultUser || "" : ""; }

  T.vault = {
    user: user,
    // The unlocked key, or null if the vault is locked on this device.
    key: async function () {
      if (!user()) return null;
      var saved = await run("readonly", function (s) { return s.get("key"); }).catch(function () { return null; });
      if (!saved || saved.user !== user() || Date.now() > saved.until) { await forget(); return null; }
      saved.until = Date.now() + IDLE_MINUTES * 60000;
      await run("readwrite", function (s) { return s.put(saved, "key"); });
      return saved.key;
    },
    record: async function () {
      var response = await fetch("/vault/key", { credentials: "same-origin" });
      if (!response.ok) throw new Error("Couldn't load your vault. Are you still logged in?");
      return response.json();
    },
    unlock: async function (secret, recovery) {
      var key = await window.TripwireVault.open(await T.vault.record(), secret, user(), recovery);
      await run("readwrite", function (s) {
        return s.put({ user: user(), key: key, until: Date.now() + IDLE_MINUTES * 60000 }, "key");
      });
      return key;
    },
    lock: forget
  };

  document.addEventListener("DOMContentLoaded", async function () {
    if (!user()) { forget(); return; }  // logged out, or no vault: nothing should stay unlocked here
    var titles = document.querySelectorAll("[data-sealed-title]");
    var state = document.querySelectorAll("[data-vault-state]");
    var key = await T.vault.key();
    state.forEach(function (el) { el.textContent = key ? "Unlocked on this device" : "Locked"; el.classList.toggle("unlocked", !!key); });
    if (!key) return;
    for (var i = 0; i < titles.length; i++) {
      try { titles[i].textContent = await window.TripwireVault.unsealText(key, titles[i].dataset.sealedTitle, user(), "title"); }
      catch (e) { titles[i].textContent = "Locked item (couldn't be unlocked)"; }
    }
  });
})();
