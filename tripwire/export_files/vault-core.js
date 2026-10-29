// Tripwire's vault: locking and unlocking, done entirely in the person's own browser.
//
// Tripwire's server only ever receives locked data. It never sees the passphrase, the
// recovery code, or the key that locks the items. Nothing here talks to the network.
//
//   vault key      a random AES-256-GCM key, made in the browser. It locks every item.
//   passphrase     chosen by the person. PBKDF2-SHA256 (600,000 rounds) turns it into a key
//                  that locks the vault key. Tripwire keeps only the vault key *locked*.
//   recovery code  random, shown once. It locks a second copy of the vault key, for when
//                  the passphrase is forgotten. Without either one, nobody can open the items.
//
// Every locked piece is "iv + ciphertext" (AES-GCM, 12-byte random iv), base64. Each piece is
// tied to the person and to what it is (title, details, file), so pieces can't be swapped.
//
// This same file runs on the website (served at /vault-core.js) and inside the offline
// unlock page in "Take my shelf with me", so a downloaded shelf opens anywhere, with no internet.
(function (root) {
  "use strict";
  var ITERATIONS = 600000;
  var subtle = root.crypto && root.crypto.subtle;
  var enc = new TextEncoder(), dec = new TextDecoder();

  function b64(bytes) {
    bytes = new Uint8Array(bytes);
    var out = "", step = 0x8000;
    for (var i = 0; i < bytes.length; i += step) out += String.fromCharCode.apply(null, bytes.subarray(i, i + step));
    return btoa(out);
  }
  function unb64(text) {
    var raw = atob(text), bytes = new Uint8Array(raw.length);
    for (var i = 0; i < raw.length; i++) bytes[i] = raw.charCodeAt(i);
    return bytes;
  }
  function random(n) { return root.crypto.getRandomValues(new Uint8Array(n)); }
  function join(a, b) { var out = new Uint8Array(a.length + b.length); out.set(a); out.set(b, a.length); return out; }
  function tag(userId, purpose) { return enc.encode("tripwire-vault:" + userId + ":" + purpose); }

  // The recovery code: 160 random bits, written as 8 groups of 4 letters and numbers
  // (no 0/O or 1/I, so it's easy to copy by hand).
  var LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
  function newRecoveryCode() {
    var bytes = random(20), bits = 0, value = 0, out = "";
    for (var i = 0; i < bytes.length; i++) {
      value = (value << 8) | bytes[i]; bits += 8;
      while (bits >= 5) { out += LETTERS[(value >>> (bits - 5)) & 31]; bits -= 5; }
    }
    return out.match(/.{4}/g).join("-");
  }
  function cleanRecoveryCode(code) { return String(code).toUpperCase().replace(/[^A-Z0-9]/g, ""); }

  async function keyFromSecret(secret, salt, iterations) {
    var base = await subtle.importKey("raw", enc.encode(secret.normalize("NFKC")), "PBKDF2", false, ["deriveKey"]);
    return subtle.deriveKey({ name: "PBKDF2", salt: unb64(salt), iterations: iterations, hash: "SHA-256" },
                            base, { name: "AES-GCM", length: 256 }, false, ["wrapKey", "unwrapKey"]);
  }
  async function lockKey(key, secret, salt, iterations, aad) {
    var iv = random(12);
    var locked = await subtle.wrapKey("raw", key, await keyFromSecret(secret, salt, iterations),
                                      { name: "AES-GCM", iv: iv, additionalData: aad });
    return b64(join(iv, new Uint8Array(locked)));
  }
  async function unlockKey(locked, secret, salt, iterations, aad) {
    var bytes = unb64(locked);
    // Not extractable: once unlocked, the key can be used on this page but never read out of it.
    return subtle.unwrapKey("raw", bytes.subarray(12), await keyFromSecret(secret, salt, iterations),
                            { name: "AES-GCM", iv: bytes.subarray(0, 12), additionalData: aad },
                            { name: "AES-GCM" }, false, ["encrypt", "decrypt"]);
  }

  // A new vault: returns { record (to store at Tripwire: all locked), recoveryCode (to show once) }.
  async function create(passphrase, userId) {
    var key = await subtle.generateKey({ name: "AES-GCM", length: 256 }, true, ["encrypt", "decrypt"]);
    var recoveryCode = newRecoveryCode();
    var record = { v: 1, kdf: "PBKDF2-SHA256", iterations: ITERATIONS, salt: b64(random(16)), recovery_salt: b64(random(16)) };
    record.key = await lockKey(key, passphrase, record.salt, record.iterations, tag(userId, "passphrase"));
    record.recovery_key = await lockKey(key, cleanRecoveryCode(recoveryCode), record.recovery_salt, record.iterations,
                                        tag(userId, "recovery"));
    return { record: record, recoveryCode: recoveryCode };
  }

  // Open the vault with the passphrase (or, with recovery = true, the recovery code).
  // A wrong one fails here: AES-GCM refuses to unlock.
  async function open(record, secret, userId, recovery) {
    try {
      return recovery
        ? await unlockKey(record.recovery_key, cleanRecoveryCode(secret), record.recovery_salt, record.iterations, tag(userId, "recovery"))
        : await unlockKey(record.key, secret, record.salt, record.iterations, tag(userId, "passphrase"));
    } catch (e) {
      throw new Error(recovery ? "That recovery code doesn't open this vault." : "That passphrase doesn't open this vault.");
    }
  }

  // A new passphrase, for a vault that's already open (needs the key, so: open with the old one
  // or the recovery code first). The key itself doesn't change, so every item stays readable.
  async function relock(record, secret, recovery, newPassphrase, userId) {
    var bytes = unb64(recovery ? record.recovery_key : record.key);
    var kek = await keyFromSecret(recovery ? cleanRecoveryCode(secret) : secret,
                                  recovery ? record.recovery_salt : record.salt, record.iterations);
    var key = await subtle.unwrapKey("raw", bytes.subarray(12), kek,
                                     { name: "AES-GCM", iv: bytes.subarray(0, 12), additionalData: tag(userId, recovery ? "recovery" : "passphrase") },
                                     { name: "AES-GCM" }, true, ["encrypt", "decrypt"]);
    var next = Object.assign({}, record, { salt: b64(random(16)) });
    next.key = await lockKey(key, newPassphrase, next.salt, next.iterations, tag(userId, "passphrase"));
    return next;
  }

  async function seal(key, data, userId, purpose) {
    var bytes = typeof data === "string" ? enc.encode(data) : new Uint8Array(data);
    var iv = random(12);
    var locked = await subtle.encrypt({ name: "AES-GCM", iv: iv, additionalData: tag(userId, purpose) }, key, bytes);
    return join(iv, new Uint8Array(locked));
  }
  async function unseal(key, sealed, userId, purpose) {
    var bytes = typeof sealed === "string" ? unb64(sealed) : new Uint8Array(sealed);
    try {
      return new Uint8Array(await subtle.decrypt({ name: "AES-GCM", iv: bytes.subarray(0, 12), additionalData: tag(userId, purpose) },
                                                 key, bytes.subarray(12)));
    } catch (e) {
      throw new Error("This item couldn't be unlocked: it was locked with a different key, or it was changed.");
    }
  }

  root.TripwireVault = {
    ITERATIONS: ITERATIONS, available: !!subtle,
    create: create, open: open, relock: relock,
    seal: seal, unseal: unseal,
    sealText: async function (key, text, userId, purpose) { return b64(await seal(key, text, userId, purpose)); },
    unsealText: async function (key, sealed, userId, purpose) { return dec.decode(await unseal(key, sealed, userId, purpose)); },
    b64: b64, unb64: unb64
  };
})(typeof window !== "undefined" ? window : globalThis);
