(function () {
  "use strict";

  // If Python already injected the config via UserScript, use it directly.
  if (window.windMgrConfigReady) return;

  window.windMgrConfig = {};
  window.windMgrConfigReady = fetch("../config.ini", { cache: "no-store" })
    .then(response => {
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.text();
    })
    .then(text => {
      window.windMgrConfig = parseIni(text);
      return window.windMgrConfig;
    })
    .catch(error => {
      console.warn("Failed to load ../config.ini; using built-in defaults", error);
      window.windMgrConfig = {};
      return window.windMgrConfig;
    });

  function parseIni(text) {
    const config = {};
    let section = null;
    text.split(/\r?\n/).forEach(line => {
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
