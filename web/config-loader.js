(function () {
  "use strict";

  // Python may inject a narrowed runtime config into WebKit before this file loads.
  if (window.windMgrConfigReady) return;

  window.windMgrConfig = {};
  window.windMgrConfigReady = Promise.all([
    fetchConfig("../config.ini"),
    fetchConfig("../config.user.ini"),
  ])
    .then(([defaultText, userText]) => mergeConfig(parseIni(defaultText), parseIni(userText)))
    .then(config => {
      window.windMgrConfig = config;
      return window.windMgrConfig;
    })
    .catch(error => {
      console.warn("Failed to load config file; using built-in defaults", error);
      window.windMgrConfig = {};
      return window.windMgrConfig;
    });

  function mergeConfig(base, override) {
    const merged = Object.assign({}, base || {});
    Object.entries(override || {}).forEach(([section, values]) => {
      merged[section] = Object.assign({}, merged[section] || {}, values || {});
    });
    return merged;
  }

  function fetchConfig(path) {
    return fetch(path, { cache: "no-store" })
      .then(response => response.ok ? response.text() : "");
  }

  function parseIni(text) {
    const config = {};
    let section = null;
    String(text || "").split(/\r?\n/).forEach(line => {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith("#") || trimmed.startsWith(";")) return;
      const sectionMatch = trimmed.match(/^\[([^\]]+)\]$/);
      if (sectionMatch) {
        section = sectionMatch[1].trim();
        config[section] = config[section] || {};
        return;
      }
      const eq = trimmed.indexOf("=");
      if (eq < 0 || !section) return;
      const key = trimmed.slice(0, eq).trim();
      const raw = trimmed.slice(eq + 1).trim();
      const num = Number(raw);
      config[section][key] = Number.isFinite(num) && raw !== "" ? num : raw;
    });
    return config;
  }
})();
