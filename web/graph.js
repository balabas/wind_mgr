/* wind_mgr — D3.js force-directed graph */
(function () {
  "use strict";

  const GRAPH_VERSION = "20260504-0260";

  // ── State ────────────────────────────────────────────────────────────────
  let _data = { nodes: [], edges: [], projects: [], active_xid: null };
  // ── Perf counters ─────────────────────────────────────────────────────────
  let _perfUpdateCount = 0, _perfSimCount = 0, _perfThumbCount = 0, _perfLastReport = Date.now();
  function _perfReport() {
    const now = Date.now();
    if (now - _perfLastReport < 10000) return;  // report every 10s
    const elapsed = ((now - _perfLastReport) / 1000).toFixed(1);
    const dom = domStats();
    const mem = (performance.memory
      ? ` jsHeap=${(performance.memory.usedJSHeapSize/1048576).toFixed(1)}MB/${(performance.memory.jsHeapSizeLimit/1048576).toFixed(0)}MB`
      : "");
    const simA = _simulation ? _simulation.alpha().toFixed(4) : "null";
    console.log(
      `[perf] last ${elapsed}s: updateGraph×${_perfUpdateCount}  simRebuild×${_perfSimCount}  thumbUpdates×${_perfThumbCount}  dom=${dom.total} nodes=${dom.cards} hulls=${dom.hulls} links=${dom.links} clips=${dom.clips} imgs=${dom.images} hidden=${dom.hidden}  simAlpha=${simA}${mem}`
    );
    _perfUpdateCount = 0; _perfSimCount = 0; _perfThumbCount = 0;
    _perfLastReport = now;
  }

  // ── Pan frame-time probe ──────────────────────────────────────────────────
  let _panFrameProbe = null;
  let _panFrameTimes = [];
  let _panFramePrev = 0;

  function startPanFrameProbe() {
    if (_panFrameProbe) return;
    _panFrameTimes = [];
    _panFramePrev = performance.now();
    function tick(ts) {
      if (!_panPerformanceActive) {
        _panFrameProbe = null;
        if (_panFrameTimes.length > 0) {
          const avg = (_panFrameTimes.reduce((a, b) => a + b, 0) / _panFrameTimes.length).toFixed(1);
          const max = Math.max(..._panFrameTimes).toFixed(1);
          const slow = _panFrameTimes.filter(t => t > 20).length;
          console.log(`[pan] frames=${_panFrameTimes.length} avgMs=${avg} maxMs=${max} slow(>20ms)=${slow} simAlpha=${_simulation ? _simulation.alpha().toFixed(4) : "null"}`);
        }
        return;
      }
      const dt = ts - _panFramePrev;
      _panFramePrev = ts;
      if (dt > 0 && dt < 500) _panFrameTimes.push(dt);
      _panFrameProbe = requestAnimationFrame(tick);
    }
    _panFrameProbe = requestAnimationFrame(tick);
  }

  function domStats() {
    if (!_svg) return { total: 0, cards: 0, hulls: 0, links: 0, clips: 0, images: 0, hidden: 0 };
    const root = _svg.node();
    return {
      total: root.querySelectorAll("*").length,
      cards: root.querySelectorAll(".node-g").length,
      hulls: root.querySelectorAll(".hull-group").length,
      links: root.querySelectorAll(".link,.link-hit").length,
      clips: root.querySelectorAll("clipPath").length,
      images: root.querySelectorAll("image").length,
      hidden: root.querySelectorAll('[display="none"],[style*="display: none"]').length,
    };
  }
  let _simulation = null;
  let _svg = null, _g = null, _overlay = null, _zoom = null;
  let _nodeMap = {};
  let _projectMap = {};
  let _selectedXid = null;
  let _initialized = false;
  let _pendingData = null;
  let _projectAnchors = {};
  let _graphSignature = "";
  let _dragFreeze = false;
  let _forceFrozen = false;
  let _ctrlDown = false;
  let _dragStart = null;
  let _dragMoved = false;
  let _dragDropHulls = null;
  let _dragActive = false;
  let _queuedGraphData = null;
  let _recentDetach = {};
  let _dragOriginProject = null;
  let _dragOriginHull = null;
  let _dragTargetProject = null;
  let _dragTargetParent = null;
  let _dragTargetLabel = null;  // {pid, name} when dragging over a cluster label
  let _settleAfterMoveUntil = 0;
  let _lastMiddleClickAt = 0;
  let _panRestoreTimer = null;
  let _panPerformanceActive = false;
  let _deferredThumbItems = [];
  let _pendingZoomTransform = null;
  let _pendingZoomUserInitiated = false;
  let _zoomFrame = null;
  let _currentZoomTransform = d3.zoomIdentity;
  let _viewCenterTarget = null;
  let _backendInteractionActive = false;
  let _backendInteractionStopTimer = null;
  let _lastHoverSentAt = {};
  let _hoveredNode = null;
  let _livePreviewNode = null;
  let _lastLivePreviewSentAt = 0;
  let _showActiveFlyState = null;
  let _appList = [];           // [{id, name, icon_url, favorite}]
  let _pendingLaunchPos = null; // {x, y} graph coords for next new card
  let _linkRouteOffsets = {};
  let _currentLinkData = [];
  let _prelayoutInitializedProjects = new Set();
  let _prevEdgeKeys = new Set(); // tracks previous render's edge set for new-edge detection

  const NODE_W  = 180;
  const THUMB_H = 140;
  const INFO_H  = 0;
  const NODE_H  = THUMB_H + INFO_H;
  const LAYOUT = {
    hullPad: 100,
    hullShape: "cards",
    hullCornerRadius: 18,
    dropIntoPad: 30,
    dropParentInnerPad: -18,
    dropHullPad: 30,
    dropNearestDistance: 0,
    geometrySpacing: 620,
    projectMargin: 260,
    projectMargin: 220,
    projectCellW: 720,
    projectCellH: 540,
    sameProjectLinkDistance: 180,
    crossProjectLinkDistance: 680,
    sameProjectLinkStrength: 0.35,
    crossProjectLinkStrength: 0.01,
    nodeCharge: -400,
    nodeCollideRadius: 10,
    cardArea: 25200,
    cardMinWidth: 110,
    cardMaxWidth: 300,
    cardMinHeight: 90,
    cardMaxHeight: 260,
    clusterStrength: 0.18,
    projectCirclePadding: 360,
    projectCircleStrength: 0.38,
    projectRectGap: 300,
    projectRectStrength: 1.05,
    foreignCardBoundaryGap: 90,
    foreignCardBoundaryStrength: 0.75,
    projectAnchorStrength: 0.035,
    centerStrength: 0.01,
    velocityDecay: 0.15,
    alphaDecay: 0.03,
    fitMarginLeft: 160,
    fitMarginRight: 160,
    fitMarginTop: 240,
    fitMarginBottom: 140,
    maxZoom: 6,
    groupLabelGap: 18,
    hierarchyGap: 260,
    hierarchyStrength: 1.15,
    hierarchySiblingSpread: 320,
    hierarchyPrelayoutEnabled: true,
    hierarchyBranchStrength: 0.45,
    hierarchyOrderStrength: 0.16,
    hierarchyCrossingStrength: 0.28,
    linkCurveSpread: 110,
    linkCardAvoidanceMargin: 40,
    linkCardAvoidanceStrength: 0.34,
    linkCardAvoidanceSamples: 10,
    clickMoveTolerancePx: 8,
    clickHoldTimeoutMs: 250,
    zoomWheelSensitivity: 0.002,
  };

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    Promise.resolve(window.windMgrConfigReady)
      .then(() => {
        const layoutConfig = normalizeLayoutConfig((window.windMgrConfig || {}).layout || {});
        Object.assign(LAYOUT, layoutConfig);
        applySpacingPreset(layoutConfig);
        _initInner();
      })
      .catch(e => { console.error("init failed:", e.toString(), e.stack || ""); });
  }

  function normalizeLayoutConfig(config) {
    const normalized = Object.assign({}, config || {});
    const aliases = {
      cardGroupSpacing: "geometrySpacing",
      cardGroupBoundaryPadding: "hullPad",
      cardGroupBoundaryShape: "hullShape",
      cardGroupBoundaryCornerRadius: "hullCornerRadius",
      dropCardGroupPadding: "dropHullPad",
      cardGroupMargin: "projectMargin",
      sameCardGroupLinkDistance: "sameProjectLinkDistance",
      sameCardGroupLinkStrength: "sameProjectLinkStrength",
      crossCardGroupLinkStrength: "crossProjectLinkStrength",
      cardRepelStrength: "nodeCharge",
      cardCollisionRadius: "nodeCollideRadius",
      cardGroupTightness: "clusterStrength",
      cardGroupCircleSeparationStrength: "projectCircleStrength",
      cardGroupRectangleSeparationStrength: "projectRectStrength",
      otherGroupProtectedGap: "foreignCardBoundaryGap",
      otherGroupPushOutStrength: "foreignCardBoundaryStrength",
      cardGroupGridStrength: "projectAnchorStrength",
      cardGroupLabelGap: "groupLabelGap",
    };
    Object.entries(aliases).forEach(([readableKey, internalKey]) => {
      if (Object.prototype.hasOwnProperty.call(normalized, readableKey)) {
        normalized[internalKey] = normalized[readableKey];
      }
    });
    return normalized;
  }

  function applySpacingPreset(config) {
    const spacing = Number(LAYOUT.geometrySpacing);
    const has = key => Object.prototype.hasOwnProperty.call(config, key);
    if (!has("projectCellW")) LAYOUT.projectCellW = spacing;
    if (!has("projectCellH")) LAYOUT.projectCellH = spacing;
    if (!has("projectRectGap")) LAYOUT.projectRectGap = Math.round(spacing * 0.42);
    if (!has("projectCirclePadding")) LAYOUT.projectCirclePadding = Math.round(spacing * 0.5);
    if (!has("crossProjectLinkDistance")) LAYOUT.crossProjectLinkDistance = Math.round(spacing * 0.95);
  }

  function _initInner() {
    _svg = d3.select("svg#graph");
    // Keep the SVG tree single-rooted even if WebKit fires DOMContentLoaded
    // again after an internal reload.
    _svg.selectAll("*").remove();

    const defs = _svg.append("defs");

    // Arrow marker for edges
    defs.append("marker")
      .attr("id", "arrowhead")
      .attr("viewBox", "0 -5 12 10")
      .attr("refX", 0).attr("refY", 0)
      .attr("markerWidth", 12).attr("markerHeight", 10)
      .attr("markerUnits", "userSpaceOnUse")
      .attr("orient", "auto")
      .append("path")
        .attr("d", "M0,-5L12,0L0,5")
        .attr("fill", "#777");

    _zoom = d3.zoom()
      .scaleExtent([0.08, LAYOUT.maxZoom])
      .wheelDelta(zoomWheelDelta)
      .filter(zoomFilter)
      .on("start", () => setPanPerformanceMode(true))
      .on("zoom", (e) => scheduleZoomTransform(e.transform, !!e.sourceEvent));
    _zoom.on("end", () => setPanPerformanceMode(false));

    _svg.call(_zoom);
    _svg.on("auxclick", (e) => {
      if (e.button !== 1) return;
      e.preventDefault();
      const now = Date.now();
      if (now - _lastMiddleClickAt < 350) {
        _lastMiddleClickAt = 0;
        fitView();
      } else {
        _lastMiddleClickAt = now;
      }
    });
    _g = _svg.append("g").attr("class", "graph-world")
      .style("will-change", "transform");

    _g.append("g").attr("class", "hulls-layer");
    _g.append("g").attr("class", "links-layer");
    _g.append("g").attr("class", "nodes-layer");
    _g.append("g").attr("class", "labels-layer");
    _overlay = _svg.append("g").attr("class", "viewport-overlay");

    document.addEventListener("click", () => { hideContextMenu(); hideLinkContextMenu(); hideRadialMenu(); });
    document.getElementById("radial-backdrop").addEventListener("click", (e) => {
      e.stopPropagation();
      hideRadialMenu();
    });
    _svg.on("click.radial", (e) => {
      // Ignore clicks on cards, links, labels — only empty space and hull fills
      const t = e.target;
      if (t.closest(".node-g") || t.closest(".link-hit") || t.closest(".cluster-label")) return;
      // Second guard: elementFromPoint catches cases where event.target ancestry diverges
      // (e.g. after D3 drag suppresses the node-g click, or pointer-events edge cases in WebKit)
      const top = document.elementFromPoint(e.clientX, e.clientY);
      if (top && top.closest(".node-g")) return;
      // Third guard: during/after pan, .graph-world has pointer-events:none so DOM hit-testing
      // is blind to cards. Fall back to graph-coordinate bounds check against the node map.
      if (_svg.classed("is-panning")) {
        const tr = _currentZoomTransform;
        const gx = (e.clientX - tr.x) / tr.k;
        const gy = (e.clientY - tr.y) / tr.k;
        for (const n of Object.values(_nodeMap)) {
          if (n.x == null) continue;
          const sz = cardSize(n);
          if (Math.abs(gx - n.x) < sz.w / 2 && Math.abs(gy - n.y) < sz.h / 2) {
            onNodeClick(n, "panning-fallback");
            return;
          }
        }
      }
      e.stopPropagation();
      hideContextMenu();
      hideLinkContextMenu();
      const tr = _currentZoomTransform;
      const graphX = (e.clientX - tr.x) / tr.k;
      const graphY = (e.clientY - tr.y) / tr.k;

      // Determine group context from the clicked hull (if any)
      const hullEl = t.closest(".hull-group");
      let context = null; // null = own new group
      if (hullEl) {
        const datum = d3.select(hullEl).datum();
        if (datum) {
          const pid = datum.pid;
          const activeXid = _data && _data.active_xid;
          const activeNode = activeXid ? _nodeMap[activeXid] : null;
          const activeInGroup = activeNode && activeNode.project_id === pid;
          context = {
            project_id: pid,
            parent_xid: activeInGroup ? activeXid : null,
          };
        }
      }

      showRadialMenu(e.clientX, e.clientY, graphX, graphY, context);
    });
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", () => { _ctrlDown = false; setForceFrozen(false); });
    document.addEventListener("keyup", onKeyUp);
    window.addEventListener("resize", () =>
      _svg.attr("width", window.innerWidth).attr("height", window.innerHeight));

    document.getElementById("btn-refresh").addEventListener("click", () =>
      sendToBackend({ action: "refresh_all_thumbs" }));
    const raiseGroupButton = document.getElementById("btn-raise-group");
    setRaiseGroupButtonState(raiseGroupButton, defaultRaiseGroupOnActivate());
    raiseGroupButton.addEventListener("click", function () {
      const enabled = !this.classList.contains("active");
      setRaiseGroupButtonState(this, enabled);
      sendToBackend({ action: "set_raise_card_group_on_card_activate", enabled });
    });
    document.getElementById("btn-fit").addEventListener("click", fitView);
    document.getElementById("btn-reset").addEventListener("click", resetLayout);
    _initialized = true;
    console.log("wind_mgr init complete graph=" + GRAPH_VERSION);
    if (_pendingData) {
      window.windMgr.updateGraph(_pendingData);
      _pendingData = null;
    }
  }

  function setRaiseGroupButtonState(button, enabled) {
    button.classList.toggle("active", enabled);
    button.querySelector(".toggle-box").textContent = enabled ? "☑" : "□";
    button.querySelector(".toggle-text").textContent = enabled ? "Raise Group: on" : "Raise Group";
    button.title = enabled
      ? "On: card click raises real windows from the same card group before activating the selected window"
      : "Off: card click only activates the selected real window";
    button.setAttribute("aria-pressed", enabled ? "true" : "false");
  }

  function defaultRaiseGroupOnActivate() {
    const activation = (window.windMgrConfig || {}).activation || {};
    return configBool(
      activation.default_raise_card_group_on_card_activate,
      configBool(
        activation.raise_card_group_on_card_activate,
        configBool(activation.raise_same_geometry_on_card_activate, false)
      )
    );
  }

  function configBool(value, fallback) {
    if (typeof value === "boolean") return value;
    if (typeof value === "number") return value !== 0;
    if (typeof value === "string") {
      const normalized = value.trim().toLowerCase();
      if (["1", "true", "yes", "on"].includes(normalized)) return true;
      if (["0", "false", "no", "off"].includes(normalized)) return false;
    }
    return fallback;
  }

  function zoomFilter(e) {
    if (e.type === "wheel") return true;
    if (e.type === "mousedown") return e.button === 1;
    if (e.type === "dblclick") return false;
    return !e.button;
  }

  function zoomWheelDelta(e) {
    const sensitivity = Number(LAYOUT.zoomWheelSensitivity) || 0.002;
    const unit = e.deltaMode === 1 ? 16 : e.deltaMode === 2 ? window.innerHeight : 1;
    return -e.deltaY * unit * sensitivity;
  }

  function setPanPerformanceMode(active) {
    if (!_svg) return;
    if (_panRestoreTimer) {
      clearTimeout(_panRestoreTimer);
      _panRestoreTimer = null;
    }
    if (active) {
      _panPerformanceActive = true;
      _svg.classed("is-panning", true);
      setBackendInteractionActive(true);
      startPanFrameProbe();
      return;
    }
    _panRestoreTimer = setTimeout(() => {
      _panPerformanceActive = false;
      _svg.classed("is-panning", false);
      applyZoomTransform(_currentZoomTransform);
      setBackendInteractionActive(false);
      if (_deferredThumbItems.length) {
        const items = _deferredThumbItems;
        _deferredThumbItems = [];
        updateThumbnails(items);
      }
      _panRestoreTimer = null;
    }, 180);
  }

  function scheduleZoomTransform(transform, userInitiated = false) {
    _pendingZoomTransform = transform;
    _pendingZoomUserInitiated = _pendingZoomUserInitiated || userInitiated;
    if (_zoomFrame) return;
    _zoomFrame = requestAnimationFrame(() => {
      _zoomFrame = null;
      if (_pendingZoomTransform) {
        _currentZoomTransform = _pendingZoomTransform;
        applyZoomTransform(_pendingZoomTransform);
        if (_pendingZoomUserInitiated) {
          rememberViewCenterTarget();
        }
        _pendingZoomTransform = null;
        _pendingZoomUserInitiated = false;
      }
    });
  }

  function applyZoomTransform(transform) {
    if (!_g || !transform) return;
    const { k, x, y } = transform;
    // matrix3d forces a GPU compositing layer in WebKit (matrix() may stay software).
    _g
      .style("transform", `matrix3d(${k},0,0,0, 0,${k},0,0, 0,0,1,0, ${x},${y},0,1)`)
      .style("transform-origin", "0 0")
      .style("transform-box", "view-box");
    // Skip getBoundingClientRect during active pan — it forces a synchronous layout flush.
    if (!_panPerformanceActive) sendLivePreviewBoundsThrottled();
  }

  function setBackendInteractionActive(active) {
    if (_backendInteractionStopTimer) {
      clearTimeout(_backendInteractionStopTimer);
      _backendInteractionStopTimer = null;
    }
    if (active) {
      if (_backendInteractionActive) return;
      _backendInteractionActive = true;
      sendToBackend({ action: "set_interaction_active", active: true });
      return;
    }
    _backendInteractionStopTimer = setTimeout(() => {
      if (!_backendInteractionActive) return;
      _backendInteractionActive = false;
      sendToBackend({ action: "set_interaction_active", active: false });
      _backendInteractionStopTimer = null;
    }, 900);
  }

  // ── Public API ────────────────────────────────────────────────────────────
  window.windMgr = {
    updateGraph(data) {
      if (!_initialized) {
        console.warn("windMgr.updateGraph called before init — queuing");
        _pendingData = data;
        return;
      }
      if (_dragActive) {
        _queuedGraphData = data;
        return;
      }
      data = reuseGraphObjects(data);
      const nextSignature = graphSignature(data);
      const topologyChanged = nextSignature !== _graphSignature;
      _data = data;
      _graphSignature = nextSignature;
      _nodeMap = {};
      _projectMap = {};
      data.nodes.forEach(n => { _nodeMap[n.xid] = n; });
      data.projects.forEach(p => { _projectMap[p.id] = p; });
      _perfUpdateCount++;
      _perfReport();
      render(topologyChanged);
      updateStatus();
    },

    setActiveWindow(xid) {
      setActiveWindow(xid);
    },

    updateThumbnails(items) {
      updateThumbnails(items);
    },

    animateActiveWindowFromScreen(payload) {
      animateActiveWindowFromScreen(payload);
    },

    highlightNode(xid) {
      const g = d3.select(`[data-xid="${xid}"]`);
      g.classed("pulse", false);
      setTimeout(() => g.classed("pulse", true), 10);
    },

    flashEdge(src, tgt) {
      d3.selectAll(".link")
        .filter(d => d.source.xid === src && d.target.xid === tgt)
        .classed("flash", true)
        .on("animationend", function () { d3.select(this).classed("flash", false); });
    },

    fitView() {
      fitView();
    },

    centerRememberedView() {
      centerRememberedView();
    },

    setSuspended(suspended) {
      if (!_svg) return;
      _svg.classed("suspended", !!suspended);
      if (suspended) {
        if (_simulation) _simulation.stop();
        _panPerformanceActive = false;
        if (_panFrameProbe) {
          cancelAnimationFrame(_panFrameProbe);
          _panFrameProbe = null;
        }
        if (_zoomFrame) {
          cancelAnimationFrame(_zoomFrame);
          _zoomFrame = null;
        }
        if (_backendInteractionStopTimer) {
          clearTimeout(_backendInteractionStopTimer);
          _backendInteractionStopTimer = null;
        }
      } else if (_simulation && _simulation.alpha() > _simulation.alphaMin()) {
        _simulation.restart();
      }
    },

    setAppList(apps) {
      _appList = apps || [];
    },
  };

  function reuseGraphObjects(data) {
    const oldNodes = _nodeMap || {};
    data.nodes = (data.nodes || []).map(n => {
      const old = oldNodes[n.xid];
      if (!old) return n;
      const keep = {
        x: old.x, y: old.y, vx: old.vx, vy: old.vy, fx: old.fx, fy: old.fy,
      };
      Object.assign(old, n);
      if (n.x == null) old.x = keep.x;
      if (n.y == null) old.y = keep.y;
      if (n.vx == null) old.vx = keep.vx;
      if (n.vy == null) old.vy = keep.vy;
      if (n.fx == null) old.fx = keep.fx;
      if (n.fy == null) old.fy = keep.fy;
      return old;
    });
    return data;
  }

  function setActiveWindow(xid) {
    if (_data.active_xid === xid) {
      applyActiveWindowClasses();
      return;
    }
    _data.active_xid = xid;
    if (_hoveredNode && _hoveredNode.xid === xid) _hoveredNode = null;
    applyActiveWindowClasses();
    const active = d3.select(`[data-xid="${xid}"]`);
    if (!active.empty()) {
      active.classed("active-window-new", false);
      requestAnimationFrame(() => active.classed("active-window-new", true));
    }
  }

  function applyActiveWindowClasses() {
    d3.selectAll(".node-g")
      .classed("active-window", d => d.xid === _data.active_xid)
      .classed("active-window-new", function (d) {
        return d.xid === _data.active_xid
          && d3.select(this).classed("active-window-new");
      });
  }

  function animateActiveWindowFromScreen(payload) {
    if (!_overlay || !payload) return;
    const xid = Number(payload.xid);
    const source = sourceViewportRect(payload);
    const fly = ensureShowActiveFly(payload, source);
    if (_showActiveFlyState && !_showActiveFlyState.started) {
      positionShowActiveFly(fly, source);
      _showActiveFlyState.source = source;
    }
    const node = _nodeMap[xid];
    if (!node || node.x == null || node.y == null) {
      _showActiveFlyState.tries += 1;
      if (_showActiveFlyState.tries > 20) {
        fly.transition().duration(180).style("opacity", 0).remove();
        _showActiveFlyState = null;
        return;
      }
      setTimeout(() => animateActiveWindowFromScreen(payload), 80);
      return;
    }
    if (_showActiveFlyState.started) return;
    if (!payload.stable_origin) return;
    _showActiveFlyState.started = true;

    const target = cardViewportRect(node);
    const duration = Math.max(100, Number(payload.duration_ms) || 650);
    const thumbUrl = payload.thumb_url || node.thumb_url || "";
    if (thumbUrl) {
      fly.select(".show-active-fly-thumb")
        .attr("href", thumbUrl)
        .style("display", null);
    }

    fly.selectAll("rect,image")
      .transition()
      .duration(duration)
      .ease(d3.easeCubicOut)
      .attr("x", target.x)
      .attr("y", target.y)
      .attr("width", target.w)
      .attr("height", target.h)
      .attr("rx", 8);

    fly.transition()
      .duration(duration)
      .ease(d3.easeCubicOut)
      .style("opacity", 1)
      .transition()
      .duration(180)
      .style("opacity", 0)
      .remove()
      .on("end", () => { _showActiveFlyState = null; });
  }

  function ensureShowActiveFly(payload, source) {
    if (_showActiveFlyState && _showActiveFlyState.xid === Number(payload.xid)) {
      return _showActiveFlyState.fly;
    }
    _overlay.selectAll(".show-active-fly").remove();

    const fly = _overlay.append("g")
      .attr("class", "show-active-fly")
      .style("pointer-events", "none")
      .style("opacity", 0.95);

    fly.append("rect")
      .attr("class", "show-active-fly-bg")
      .attr("x", source.x)
      .attr("y", source.y)
      .attr("width", source.w)
      .attr("height", source.h)
      .attr("rx", 10);

    fly.append("image")
      .attr("class", "show-active-fly-thumb")
      .attr("href", payload.thumb_url || "")
      .attr("x", source.x)
      .attr("y", source.y)
      .attr("width", source.w)
      .attr("height", source.h)
      .attr("preserveAspectRatio", "xMidYMid slice")
      .style("display", payload.thumb_url ? null : "none");

    fly.append("rect")
      .attr("class", "show-active-fly-border")
      .attr("x", source.x)
      .attr("y", source.y)
      .attr("width", source.w)
      .attr("height", source.h)
      .attr("rx", 10);

    _showActiveFlyState = {
      xid: Number(payload.xid),
      fly,
      source,
      started: false,
      tries: 0,
    };
    return fly;
  }

  function positionShowActiveFly(fly, source) {
    fly.selectAll("rect,image")
      .interrupt()
      .attr("x", source.x)
      .attr("y", source.y)
      .attr("width", source.w)
      .attr("height", source.h);
  }

  function cardViewportRect(node) {
    const size = cardSize(node);
    const t = _currentZoomTransform || d3.zoomIdentity;
    return {
      x: (Number(node.x) - size.w / 2) * t.k + t.x,
      y: (Number(node.y) - size.h / 2) * t.k + t.y,
      w: size.w * t.k,
      h: size.h * t.k,
    };
  }

  function sourceViewportRect(payload) {
    if (payload.stable_origin) {
      return {
        x: Number(payload.x) || 0,
        y: Number(payload.y) || 0,
        w: Math.max(1, Number(payload.w) || 1),
        h: Math.max(1, Number(payload.h) || 1),
      };
    }
    const screenOrigin = currentViewportScreenOrigin();
    return {
      x: (Number(payload.screen_x) || 0) - screenOrigin.x,
      y: (Number(payload.screen_y) || 0) - screenOrigin.y,
      w: Math.max(1, Number(payload.w) || 1),
      h: Math.max(1, Number(payload.h) || 1),
    };
  }

  function currentViewportScreenOrigin() {
    const sx = Number(window.screenX ?? window.screenLeft ?? 0);
    const sy = Number(window.screenY ?? window.screenTop ?? 0);
    const outerW = Number(window.outerWidth || window.innerWidth || 0);
    const outerH = Number(window.outerHeight || window.innerHeight || 0);
    const chromeX = Math.max(0, (outerW - window.innerWidth) / 2);
    const chromeY = Math.max(0, outerH - window.innerHeight - chromeX);
    return { x: sx + chromeX, y: sy + chromeY };
  }

  function graphSignature(data) {
    const nodes = (data.nodes || [])
      .filter(n => n.is_alive)
      .map(n => `${n.xid}:${n.project_id}`)
      .sort()
      .join("|");
    const edges = (data.edges || [])
      .map(e => `${e.source}->${e.target}`)
      .sort()
      .join("|");
    return `${nodes}#${edges}`;
  }

  // ── Render ────────────────────────────────────────────────────────────────
  function render(topologyChanged) {
    const nodes = _data.nodes.filter(n => n.is_alive);
    const edges = _data.edges.filter(e => {
      const s = _nodeMap[e.source], t = _nodeMap[e.target];
      return s && t && s.is_alive && t.is_alive;
    });

    _projectAnchors = computeProjectAnchors(nodes);

    const xidToNode = {};
    nodes.forEach(n => { xidToNode[n.xid] = n; });

    // First pass: restore positions for existing nodes; detect project/edge changes.
    const movedToProjects = new Set();
    // Detect children that gained a new parent this update — clear their position so
    // they get re-seeded below the parent instead of staying wherever they were.
    const newEdgeKeys = new Set(edges.map(e => `${e.source}->${e.target}`));
    const newChildXids = new Set();
    if (topologyChanged) {
      newEdgeKeys.forEach(k => {
        if (!_prevEdgeKeys.has(k)) {
          const targetXid = Number(k.split("->")[1]);
          newChildXids.add(targetXid);
        }
      });
    }
    _prevEdgeKeys = newEdgeKeys;

    nodes.forEach(n => {
      const sel = _g.select(`[data-xid="${n.xid}"]`);
      const old = sel.node() ? sel.datum() : null;
      if (old) {
        if (topologyChanged && old.project_id !== n.project_id) {
          // Node moved to a different group — clear position so it gets re-seeded
          // and invalidate the destination project so prelayout re-runs.
          movedToProjects.add(n.project_id);
        } else if (newChildXids.has(n.xid)) {
          // Node gained a new parent — clear position so it seeds below its parent.
        } else {
          n.x = old.x; n.y = old.y; n.vx = 0; n.vy = 0;
        }
      }
    });
    movedToProjects.forEach(pid => _prelayoutInitializedProjects.delete(pid));

    // Second pass: seed new/moved nodes
    const pendingPos = _pendingLaunchPos;
    _pendingLaunchPos = null;
    let pendingUsed = false;
    nodes.forEach(n => {
      if (n.x != null) return;
      const par = xidToNode[n.parent_xid];
      const anchor = _projectAnchors[n.project_id];
      if (par && par.x != null) {
        // Place below parent so hierarchy force starts near equilibrium (never above parent).
        const gapY = LAYOUT.hierarchyGap != null ? LAYOUT.hierarchyGap : 160;
        n.x = par.x + (Math.random() - 0.5) * 80;
        n.y = par.y + gapY;
      } else if (pendingPos && !pendingUsed) {
        // No parent: place at the click/launch position.
        n.x = pendingPos.x;
        n.y = pendingPos.y;
        pendingUsed = true;
      } else {
        const base = anchor || { x: window.innerWidth / 2, y: window.innerHeight / 2 };
        n.x = base.x + (Math.random() - 0.5) * 100;
        n.y = base.y + (Math.random() - 0.5) * 100;
      }
      n.vx = 0; n.vy = 0;
    });
    if (topologyChanged && !_dragActive && LAYOUT.hierarchyPrelayoutEnabled !== false) {
      const projectIds = Array.from(new Set(nodes.map(n => n.project_id)));
      const uninitialized = new Set(projectIds.filter(pid => !_prelayoutInitializedProjects.has(pid)));
      if (uninitialized.size) {
        applyHierarchyPrelayout(nodes, edges, xidToNode, uninitialized);
      }
      projectIds.forEach(pid => _prelayoutInitializedProjects.add(pid));
    }
    ensureInitialViewCenterTarget(nodes);

    // ── Nodes DOM ───────────────────────────────────────────────────────
    const node = _g.select(".nodes-layer")
      .selectAll(".node-g")
      .data(nodes, d => d.xid);

    const nodeEnter = node.enter()
      .append("g")
        .attr("class", "node-g")
        .attr("data-xid", d => d.xid)
        .on("pointerdown", (e, d) => onNodePointerDown(e, d))
        .on("pointerup", (e, d) => onNodePointerUp(e, d))
        .on("pointercancel", (_e, d) => discardPendingClick(d, "pointer-cancel"))
        .on("click", (e, d) => {
          e.stopPropagation();
          if (d._activatedFromPointer || d._activatedFromDragEnd) {
            d._activatedFromPointer = false;
            d._activatedFromDragEnd = false;
            return;
          }
          if (d._discardClickUntil && Date.now() < d._discardClickUntil) {
            return;
          }
          onNodeClick(d, "click");
        })
        .on("mouseenter", (_e, d) => onNodeHover(d))
        .on("mouseleave", (_e, d) => onNodeLeave(d))
        .on("contextmenu", (e, d) => { e.preventDefault(); showContextMenu(e, d); })
        .call(d3.drag()
          .filter(e => !e.button)
          .on("start", dragStarted)
          .on("drag",  dragged)
          .on("end",   dragEnded));

    nodeEnter.append("rect").attr("class", "node-bg");
    nodeEnter.append("image")
      .attr("class", "node-thumb")
      .attr("preserveAspectRatio", "xMidYMid meet");
    nodeEnter.append("rect").attr("class", "active-overlay");
    nodeEnter.append("image").attr("class", "node-icon").attr("width", 20).attr("height", 20);
    nodeEnter.append("title");
    nodeEnter.append("path").attr("class", "node-title-bg");
    nodeEnter.append("text").attr("class", "node-title").attr("text-anchor", "middle").attr("x", 0);

    nodeEnter.merge(node).each(function (d) { renderCard(d3.select(this), d); });
    node.exit().remove();

    // Non-topology updates (thumbnails, titles): skip simulation rebuild to avoid memory/CPU leak
    if (!topologyChanged) {
      renderNodesOnly();
      renderLinksOnly();
      renderHulls();
      return;
    }
    _perfSimCount++;

    const linkData = edges
      .map(e => ({ source: xidToNode[e.source], target: xidToNode[e.target] }))
      .filter(e => e.source && e.target);
    _currentLinkData = linkData;
    _linkRouteOffsets = computeLinkRouteOffsets(linkData);

    // ── Edges ───────────────────────────────────────────────────────────
    const linkKey = d => `${d.source.xid}-${d.target.xid}`;
    const link = _g.select(".links-layer")
      .selectAll(".link").data(linkData, linkKey);
    link.enter().append("path").attr("class", "link").merge(link);
    link.exit().remove();

    const linkHit = _g.select(".links-layer")
      .selectAll(".link-hit").data(linkData, linkKey);
    linkHit.enter().append("path")
      .attr("class", "link-hit")
      .on("contextmenu", (e, d) => { e.preventDefault(); e.stopPropagation(); showLinkContextMenu(e, d); })
      .merge(linkHit);
    linkHit.exit().remove();

    // ── Simulation (rebuilt only on topology changes) ────────────────────
    if (_simulation) _simulation.stop();
    _simulation = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(linkData).id(d => d.xid)
        .distance(d => d.source.project_id === d.target.project_id
          ? LAYOUT.sameProjectLinkDistance : LAYOUT.crossProjectLinkDistance)
        .strength(d => d.source.project_id === d.target.project_id
          ? LAYOUT.sameProjectLinkStrength : LAYOUT.crossProjectLinkStrength))
      .force("charge", d3.forceManyBody().strength(LAYOUT.nodeCharge))
      .force("collide", d3.forceCollide(d => cardRadius(d) + LAYOUT.nodeCollideRadius))
      .force("cluster", forceCluster(nodes))
      .force("projectCollide", forceProjectCollide(nodes))
      .force("projectRectCollide", forceProjectRectCollide(nodes))
      .force("projectBounds", forceProjectBounds(nodes))
      .force("projectX", d3.forceX(d => (_projectAnchors[d.project_id] || {}).x || window.innerWidth / 2).strength(LAYOUT.projectAnchorStrength))
      .force("projectY", d3.forceY(d => (_projectAnchors[d.project_id] || {}).y || window.innerHeight / 2).strength(LAYOUT.projectAnchorStrength))
      .force("hierarchy", forceHierarchy(linkData))
      .force("linkCardAvoidance", forceLinkCardAvoidance(nodes, linkData))
      .velocityDecay(LAYOUT.velocityDecay)
      .alphaDecay(LAYOUT.alphaDecay)
      .on("tick", ticked);

    if (_forceFrozen) {
      _simulation.stop();
      _simulation.alpha(0).alphaTarget(0);
      nodes.forEach(n => { n.vx = 0; n.vy = 0; });
    } else {
      _simulation.alphaTarget(0).alpha(0.65).restart();
    }
  }

  function renderCard(g, d) {
    g.classed("active-window", d.xid === _data.active_xid)
     .classed("active-window-new", d.xid === _data.active_xid && g.classed("active-window-new"))
     .classed("selected",      d.xid === _selectedXid);

    const size = cardSize(d);
    const hw = size.w / 2, hh = size.h / 2;
    const displayTitle = d.card_name || d.tab_title || d.project_name || d.title;
    const truncTitle = displayTitle.length > 24 ? displayTitle.slice(0, 23) + "…" : displayTitle;

    g.select(".node-bg")
      .attr("x", -hw).attr("y", -hh)
      .attr("width", size.w).attr("height", size.h)
      .attr("rx", 8);

    // Thumbnail
    g.select(".node-thumb")
      .attr("href", d.thumb_url || "")
      .attr("x", -hw).attr("y", -hh)
      .attr("width", size.w).attr("height", size.h)
      .style("display", d.thumb_url ? null : "none");

    g.select(".active-overlay")
      .attr("x", -hw).attr("y", -hh)
      .attr("width", size.w).attr("height", size.h)
      .attr("rx", 8);

    // Thumb placeholder emoji when no image
    // (handled by SVG text fallback if thumb fails — use onerror equivalent via error event)

    // App icon
    const hasIcon = !!d.icon_url;
    g.select(".node-icon")
      .attr("href", d.icon_url || "")
      .attr("x", hw - 24)
      .attr("y", hh - 24)
      .style("display", hasIcon ? null : "none");

    // Title background bar — rounded only at bottom, same radius/coords as card
    const titleBarH = 22, tbr = 8;
    const tbx0 = -hw, tbx1 = hw, tby0 = hh - titleBarH, tby1 = hh;
    g.select(".node-title-bg")
      .attr("d", `M${tbx0},${tby0}H${tbx1}V${tby1 - tbr}Q${tbx1},${tby1} ${tbx1 - tbr},${tby1}H${tbx0 + tbr}Q${tbx0},${tby1} ${tbx0},${tby1 - tbr}Z`);

    // Title — center in info bar
    g.select(".node-title")
      .attr("y", hh - 8)
      .text(truncTitle);

    const hoverLines = [
      displayTitle || d.title || `Window ${d.xid}`,
      d.active_file ? `File: ${d.active_file}` : "",
      d.active_directory ? `Directory: ${d.active_directory}` : "",
    ].filter(Boolean);
    g.select("title").text(hoverLines.join("\n"));
  }

  function updateThumbnails(items) {
    if (_panPerformanceActive) {
      const immediate = (items || []).filter(item => isStreamingThumbReason(item.reason));
      const deferred = (items || []).filter(item => !isStreamingThumbReason(item.reason));
      if (deferred.length) {
        _deferredThumbItems = mergeThumbnailItems(_deferredThumbItems, deferred);
      }
      if (!immediate.length) return;
      items = immediate;
    }
    _perfThumbCount += (items || []).length;
    _perfReport();
    (items || []).forEach(item => {
      const node = _nodeMap[item.xid];
      if (!node) return;
      if (Object.prototype.hasOwnProperty.call(item, "thumb_url")) {
        node.thumb_url = item.thumb_url;
      }
      if (Object.prototype.hasOwnProperty.call(item, "icon_url")) {
        node.icon_url = item.icon_url;
      }
      const g = _g.select(".nodes-layer")
        .selectAll(".node-g")
        .filter(d => d.xid === item.xid);
      if (g.empty()) return;
      if (Object.prototype.hasOwnProperty.call(item, "thumb_url")) {
        g.select(".node-thumb")
          .attr("href", node.thumb_url || "")
          .style("display", node.thumb_url ? null : "none");
      }
      if (Object.prototype.hasOwnProperty.call(item, "icon_url")) {
        const size = cardSize(node);
        g.select(".node-icon")
          .attr("href", node.icon_url || "")
          .attr("x", size.w / 2 - 24)
          .attr("y", size.h / 2 - 24)
          .style("display", node.icon_url ? null : "none");
      }
    });
  }

  function isStreamingThumbReason(reason) {
    return reason === "hover" || reason === "active";
  }

  function mergeThumbnailItems(existing, incoming) {
    const byXid = {};
    existing.forEach(item => { byXid[item.xid] = item; });
    incoming.forEach(item => { byXid[item.xid] = Object.assign(byXid[item.xid] || {}, item); });
    return Object.values(byXid);
  }

  function cardSize(d) {
    const rawW = Number(d.window_width) || NODE_W;
    const rawH = Number(d.window_height) || NODE_H;
    const ratio = Math.max(0.35, Math.min(3.8, rawW / rawH));
    let h = Math.sqrt(LAYOUT.cardArea / ratio);
    let w = h * ratio;

    const shrink = Math.min(LAYOUT.cardMaxWidth / w, LAYOUT.cardMaxHeight / h);
    if (shrink < 1) {
      w *= shrink;
      h *= shrink;
    }

    const grow = Math.max(LAYOUT.cardMinWidth / w, LAYOUT.cardMinHeight / h);
    if (grow > 1) {
      w *= grow;
      h *= grow;
    }

    return {
      w: Math.max(1, Math.min(LAYOUT.cardMaxWidth, w)),
      h: Math.max(1, Math.min(LAYOUT.cardMaxHeight, h)),
    };
  }

  function cardRadius(d) {
    const size = cardSize(d);
    return Math.hypot(size.w, size.h) / 2;
  }

  // Returns the point on the boundary of rect (cx,cy,hw,hh) facing toward (tx,ty)
  function rectEdgePoint(cx, cy, tx, ty, hw, hh) {
    const dx = tx - cx, dy = ty - cy;
    if (!dx && !dy) return [cx, cy];
    const t = Math.min(hw / Math.abs(dx), hh / Math.abs(dy));
    return [cx + dx * t, cy + dy * t];
  }

  function pointBeforeTarget(x, y, fromX, fromY, distance) {
    const dx = x - fromX, dy = y - fromY;
    const len = Math.hypot(dx, dy);
    if (!len) return [x, y];
    return [x - (dx / len) * distance, y - (dy / len) * distance];
  }

  function _linkPath(d) {
    const pts = linkPolyline(d, 1);
    if (!pts.length) return "";
    if (pts.length === 2) {
      const [x1, y1] = pts[0];
      const [x2, y2] = pointBeforeTarget(pts[1][0], pts[1][1], x1, y1, 12);
      return `M${x1},${y1}L${x2},${y2}`;
    }
    const [x1, y1] = pts[0];
    const [c1x, c1y] = pts[1];
    const [c2x, c2y] = pts[2];
    const [tx, ty] = pts[3];
    const [ex, ey] = pointBeforeTarget(tx, ty, c2x, c2y, 12);
    return `M${x1},${y1}C${c1x},${c1y} ${c2x},${c2y} ${ex},${ey}`;
  }

  function linkPolyline(d, samples) {
    const sx = d.source.x || 0, sy = d.source.y || 0;
    const tx = d.target.x || 0, ty = d.target.y || 0;
    const ss = cardSize(d.source), ts = cardSize(d.target);
    if (d.source.project_id === d.target.project_id) {
      const route = _linkRouteOffsets[`${d.source.xid}-${d.target.xid}`] || { offset: 0 };
      const sourceOffset = Math.max(-ss.w * 0.48, Math.min(ss.w * 0.48, route.offset * 0.65));
      const x1 = sx + sourceOffset;
      const y1 = sy + ss.h / 2;
      const x2 = tx;
      const y2 = ty - ts.h / 2;
      const curve = Math.max(60, Math.min(220, Math.abs(y2 - y1) * 0.55));
      // Keep lane separation near the parent, but make the final segment
      // vertical so the arrowhead points into the target card instead of
      // sideways.
      const c1x = x1 + route.offset * 0.75;
      const c2x = x2;
      const c1 = [c1x, y1 + curve];
      const c2 = [c2x, y2 - curve];
      const end = [x2, y2];
      if (samples <= 1) return [[x1, y1], c1, c2, end];
      const pts = [];
      for (let i = 0; i <= samples; i++) {
        pts.push(cubicPoint([x1, y1], c1, c2, end, i / samples));
      }
      return pts;
    }
    const [x1, y1] = rectEdgePoint(sx, sy, tx, ty, ss.w / 2, ss.h / 2);
    const [x2, y2] = rectEdgePoint(tx, ty, sx, sy, ts.w / 2, ts.h / 2);
    return [[x1, y1], [x2, y2]];
  }

  function cubicPoint(p0, p1, p2, p3, t) {
    const mt = 1 - t;
    const a = mt * mt * mt;
    const b = 3 * mt * mt * t;
    const c = 3 * mt * t * t;
    const d = t * t * t;
    return [
      a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0],
      a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1],
    ];
  }

  function computeLinkRouteOffsets(linkData) {
    const offsets = {};
    const byParent = new Map();
    linkData.forEach(link => {
      if (!link.source || !link.target) return;
      if (link.source.project_id !== link.target.project_id) return;
      const parentXid = link.source.xid;
      if (!byParent.has(parentXid)) byParent.set(parentXid, []);
      byParent.get(parentXid).push(link);
    });
    byParent.forEach(links => {
      links
        .slice()
        .sort((a, b) => ((a.target.x || 0) - (b.target.x || 0)) || String(a.target.xid).localeCompare(String(b.target.xid)))
        .forEach((link, i, arr) => {
          const rank = i - (arr.length - 1) / 2;
          offsets[`${link.source.xid}-${link.target.xid}`] = {
            offset: rank * (LAYOUT.linkCurveSpread || 0),
          };
        });
    });
    return offsets;
  }

  function ticked() {
    renderLinksOnly();

    renderNodesOnly();
    renderHulls();
  }

  function renderLinksOnly() {
    _linkRouteOffsets = computeLinkRouteOffsets(_currentLinkData);
    _g.select(".links-layer").selectAll(".link").attr("d", _linkPath);
    _g.select(".links-layer").selectAll(".link-hit").attr("d", _linkPath);
  }

  function renderNodesOnly() {
    _g.select(".nodes-layer").selectAll(".node-g")
      .attr("transform", d => `translate(${d.x || 0},${d.y || 0})`);
    sendLivePreviewBoundsThrottled();
  }

  // ── Cluster hulls ─────────────────────────────────────────────────────────
  function renderHulls() {
    const projectGroups = {};
    _data.nodes.filter(n => n.is_alive).forEach(n => {
      (projectGroups[n.project_id] = projectGroups[n.project_id] || []).push(n);
    });

    const hullData = Object.entries(projectGroups)
      .map(([pid, nodes]) => buildVisualHullDatum(pid, nodes))
      .filter(d => d.path);

    if (_dragActive && _dragOriginHull) {
      const idx = hullData.findIndex(h => h.pid === _dragOriginHull.pid);
      if (idx >= 0) hullData[idx] = _dragOriginHull;
      else hullData.push(_dragOriginHull);
    }

    const hulls = _g.select(".hulls-layer")
      .selectAll(".hull-group").data(hullData, d => d.pid);

    const enter = hulls.enter().append("g").attr("class", "hull-group");
    enter.append("path").attr("class", "cluster-hull")
      .on("dblclick", (e, d) => toggleProject(d.pid));

    const all = enter.merge(hulls);
    all.select(".cluster-hull")
      .attr("d", d => d.path)
      .attr("fill", d => d.proj.color)
      .attr("stroke", d => d.proj.color);

    const labels = _g.select(".labels-layer")
      .selectAll(".cluster-label").data(hullData, d => d.pid);
    const labelsEnter = labels.enter().append("text").attr("class", "cluster-label")
      .on("dblclick", (e, d) => {
        e.preventDefault();
        e.stopPropagation();
        renameProject(d.pid);
      })
      .on("contextmenu", (e, d) => {
        e.preventDefault();
        e.stopPropagation();
        renameProject(d.pid);
      });
    labelsEnter.merge(labels)
      .attr("x", d => d.cx).attr("y", d => d.labelY)
      .attr("text-anchor", "middle")
      .attr("fill", d => d.proj.color)
      .attr("title", "Double-click to rename")
      .text(d => d.proj.name);

    hulls.exit().remove();
    labels.exit().remove();
  }

  function buildVisualHullDatum(pid, nodes) {
    const proj = _projectMap[pid] || { id: pid, name: pid, color: "#888" };
    const pts = nodes.flatMap(n => {
      const x = n.x || 0, y = n.y || 0, pad = LAYOUT.hullPad;
      const size = cardSize(n), hw = size.w / 2, hh = size.h / 2;
      return [[x-hw-pad,y-hh-pad],[x+hw+pad,y-hh-pad],[x+hw+pad,y+hh+pad],[x-hw-pad,y+hh+pad]];
    });
    const hull = d3.polygonHull(pts);
    const path = LAYOUT.hullShape === "cards"
      ? nodes.map(n => cardRectPath(n, LAYOUT.hullPad, LAYOUT.hullCornerRadius)).join("")
      : roundedPolygonPath(hull, LAYOUT.hullCornerRadius);
    return {
      pid,
      proj,
      hull,
      path,
      nodes,
      cx: d3.mean(nodes, n => n.x),
      cy: d3.mean(nodes, n => n.y),
      labelY: d3.min(pts, p => p[1]) - LAYOUT.groupLabelGap,
    };
  }

  function cardRectPath(d, pad, radius) {
    const x = d.x || 0, y = d.y || 0;
    const size = cardSize(d), hw = size.w / 2 + pad, hh = size.h / 2 + pad;
    return roundedPolygonPath([
      [x - hw, y - hh],
      [x + hw, y - hh],
      [x + hw, y + hh],
      [x - hw, y + hh],
    ], radius);
  }

  function roundedPolygonPath(points, radius) {
    if (!points || points.length < 3) return "";
    const r = Math.max(0, Number(radius) || 0);
    if (!r) return "M" + points.join("L") + "Z";
    const parts = [];
    for (let i = 0; i < points.length; i++) {
      const prev = points[(i - 1 + points.length) % points.length];
      const curr = points[i];
      const next = points[(i + 1) % points.length];
      const prevLen = Math.hypot(curr[0] - prev[0], curr[1] - prev[1]);
      const nextLen = Math.hypot(next[0] - curr[0], next[1] - curr[1]);
      const corner = Math.min(r, prevLen / 2, nextLen / 2);
      const p1 = pointToward(curr, prev, corner);
      const p2 = pointToward(curr, next, corner);
      if (i === 0) parts.push(`M${p1[0]},${p1[1]}`);
      else parts.push(`L${p1[0]},${p1[1]}`);
      parts.push(`Q${curr[0]},${curr[1]} ${p2[0]},${p2[1]}`);
    }
    return parts.join("") + "Z";
  }

  function pointToward(from, to, distance) {
    const dx = to[0] - from[0], dy = to[1] - from[1];
    const len = Math.hypot(dx, dy) || 1;
    return [
      from[0] + dx / len * distance,
      from[1] + dy / len * distance,
    ];
  }

  // ── Force cluster ─────────────────────────────────────────────────────────
  function computeProjectAnchors(nodes) {
    const ids = Array.from(new Set(nodes.map(n => n.project_id))).sort();
    const anchors = {};
    if (!ids.length) return anchors;

    const cols = Math.ceil(Math.sqrt(ids.length));
    const rows = Math.ceil(ids.length / cols);
    const marginX = LAYOUT.projectMargin, marginY = LAYOUT.projectMargin;
    const cellW = LAYOUT.projectCellW, cellH = LAYOUT.projectCellH;
    // Use natural grid size, capped to viewport so groups don't anchor outside.
    const width = Math.min(cellW * cols, window.innerWidth - marginX * 2);
    const height = Math.min(cellH * rows, window.innerHeight - marginY * 2);

    ids.forEach((pid, i) => {
      const col = i % cols;
      const row = Math.floor(i / cols);
      anchors[pid] = {
        x: marginX + width * (col + 0.5) / cols,
        y: marginY + height * (row + 0.5) / rows,
      };
    });
    return anchors;
  }

  function applyHierarchyPrelayout(nodes, edges, xidToNode, projectFilter = null) {
    const gapX = LAYOUT.hierarchySiblingSpread != null ? LAYOUT.hierarchySiblingSpread : 260;
    const gapY = LAYOUT.hierarchyGap != null ? LAYOUT.hierarchyGap : 220;
    const projectGroups = new Map();
    nodes.forEach(n => {
      if (!projectGroups.has(n.project_id)) projectGroups.set(n.project_id, []);
      projectGroups.get(n.project_id).push(n);
    });

    projectGroups.forEach((projectNodes, projectId) => {
      if (projectFilter && !projectFilter.has(projectId)) return;
      const childMap = new Map();
      const hasParent = new Set();
      edges.forEach(e => {
        const source = xidToNode[e.source];
        const target = xidToNode[e.target];
        if (!source || !target) return;
        if (source.project_id !== projectId || target.project_id !== projectId) return;
        if (!childMap.has(source.xid)) childMap.set(source.xid, []);
        childMap.get(source.xid).push(target);
        hasParent.add(target.xid);
      });

      const roots = projectNodes
        .filter(n => !hasParent.has(n.xid))
        .sort((a, b) => ((a.x || 0) - (b.x || 0)) || String(a.xid).localeCompare(String(b.xid)));
      if (!roots.length) return;

      const widthMemo = new Map();
      function childrenOf(node) {
        return (childMap.get(node.xid) || [])
          .slice()
          .sort((a, b) => ((a.x || 0) - (b.x || 0)) || String(a.xid).localeCompare(String(b.xid)));
      }
      function subtreeWidth(node, seen = new Set()) {
        if (seen.has(node.xid)) return gapX;
        if (widthMemo.has(node.xid)) return widthMemo.get(node.xid);
        seen.add(node.xid);
        const children = childrenOf(node);
        const own = Math.max(cardSize(node).w + gapX * 0.4, gapX);
        const childWidth = children.length
          ? children.reduce((sum, child) => sum + subtreeWidth(child, new Set(seen)), 0) + gapX * 0.35 * (children.length - 1)
          : 0;
        const width = Math.max(own, childWidth);
        widthMemo.set(node.xid, width);
        return width;
      }
      function place(node, x, y, seen = new Set()) {
        if (seen.has(node.xid)) return;
        seen.add(node.xid);
        node.x = x;
        node.y = y;
        node.vx = 0;
        node.vy = 0;
        const children = childrenOf(node);
        if (!children.length) return;
        const widths = children.map(child => subtreeWidth(child));
        const total = widths.reduce((a, b) => a + b, 0) + gapX * 0.35 * (children.length - 1);
        let cursor = x - total / 2;
        children.forEach((child, i) => {
          const childX = cursor + widths[i] / 2;
          place(child, childX, y + gapY, new Set(seen));
          cursor += widths[i] + gapX * 0.35;
        });
      }

      const anchor = _projectAnchors[projectId] || { x: window.innerWidth / 2, y: window.innerHeight / 2 };
      const rootWidths = roots.map(root => subtreeWidth(root));
      const total = rootWidths.reduce((a, b) => a + b, 0) + gapX * 0.45 * (roots.length - 1);
      let cursor = anchor.x - total / 2;
      roots.forEach((root, i) => {
        const rootX = cursor + rootWidths[i] / 2;
        place(root, rootX, anchor.y - gapY * 0.35);
        cursor += rootWidths[i] + gapX * 0.45;
      });
    });
  }

  function forceHierarchy(linkData) {
    const gap = LAYOUT.hierarchyGap != null ? LAYOUT.hierarchyGap : 120;
    const strength = LAYOUT.hierarchyStrength != null ? LAYOUT.hierarchyStrength : 0.2;
    const siblingSpread = LAYOUT.hierarchySiblingSpread != null ? LAYOUT.hierarchySiblingSpread : 160;
    const branchStrength = LAYOUT.hierarchyBranchStrength != null ? LAYOUT.hierarchyBranchStrength : 0.35;
    const orderStrength = LAYOUT.hierarchyOrderStrength != null ? LAYOUT.hierarchyOrderStrength : 0.12;
    const crossingStrength = LAYOUT.hierarchyCrossingStrength != null ? LAYOUT.hierarchyCrossingStrength : 0.18;
    const sameGroupChildren = new Map();
    linkData.forEach(e => {
      const p = e.source, c = e.target;
      if (!p || !c || p.project_id !== c.project_id) return;
      if (!sameGroupChildren.has(p.xid)) sameGroupChildren.set(p.xid, []);
      sameGroupChildren.get(p.xid).push(c);
    });

    function branchNodes(root) {
      const nodes = [];
      const stack = [root];
      const seen = new Set();
      while (stack.length) {
        const n = stack.pop();
        if (!n || seen.has(n.xid)) continue;
        seen.add(n.xid);
        nodes.push(n);
        (sameGroupChildren.get(n.xid) || []).forEach(child => stack.push(child));
      }
      return nodes;
    }

    function branchCenterX(root) {
      const branch = branchNodes(root);
      return branch.reduce((sum, n) => sum + (n.x || 0), 0) / Math.max(1, branch.length);
    }

    return function force(alpha) {
      linkData.forEach(e => {
        const p = e.source, c = e.target;
        if (!p || !c) return;
        if (p.project_id !== c.project_id) return;
        const dy = (c.y || 0) - (p.y || 0);
        if (dy < gap) {
          const push = (gap - dy) * strength * alpha;
          c.vy += push;
          p.vy -= push * 0.12;
        }
      });
      sameGroupChildren.forEach((children, parentXid) => {
        const parent = linkData.find(e => e.source && e.source.xid === parentXid)?.source;
        if (!parent || children.length < 2) return;
        children
          .slice()
          .sort((a, b) => branchCenterX(a) - branchCenterX(b) || String(a.xid).localeCompare(String(b.xid)))
          .forEach((child, i, arr) => {
            const targetX = (parent.x || 0) + (i - (arr.length - 1) / 2) * siblingSpread;
            const branch = branchNodes(child);
            const branchCx = branch.reduce((sum, n) => sum + (n.x || 0), 0) / Math.max(1, branch.length);
            const shift = (targetX - branchCx) * branchStrength * alpha;
            branch.forEach((n, depth) => {
              const factor = depth === 0 ? 1 : 0.65;
              n.vx += shift * factor;
            });
          });
        const ordered = children.slice().sort((a, b) => branchCenterX(a) - branchCenterX(b));
        for (let i = 0; i < ordered.length - 1; i++) {
          const left = ordered[i];
          const right = ordered[i + 1];
          const leftCx = branchCenterX(left);
          const rightCx = branchCenterX(right);
          const minGap = siblingSpread * 0.75;
          if (rightCx - leftCx >= minGap) continue;
          const push = (minGap - (rightCx - leftCx)) * orderStrength * alpha;
          branchNodes(left).forEach(n => { n.vx -= push; });
          branchNodes(right).forEach(n => { n.vx += push; });
        }
      });
      const sameGroupLinks = linkData.filter(e => (
        e.source && e.target && e.source.project_id === e.target.project_id
      ));
      for (let i = 0; i < sameGroupLinks.length; i++) {
        for (let j = i + 1; j < sameGroupLinks.length; j++) {
          const a = sameGroupLinks[i];
          const b = sameGroupLinks[j];
          if (a.source.xid === b.source.xid || a.target.xid === b.target.xid) continue;
          const ay1 = Math.min(a.source.y || 0, a.target.y || 0);
          const ay2 = Math.max(a.source.y || 0, a.target.y || 0);
          const by1 = Math.min(b.source.y || 0, b.target.y || 0);
          const by2 = Math.max(b.source.y || 0, b.target.y || 0);
          if (Math.min(ay2, by2) - Math.max(ay1, by1) < gap * 0.25) continue;

          const sourceOrder = (a.source.x || 0) - (b.source.x || 0);
          const targetOrder = branchCenterX(a.target) - branchCenterX(b.target);
          if (Math.abs(sourceOrder) < 20 || sourceOrder * targetOrder >= 0) continue;

          const desired = Math.sign(sourceOrder) * siblingSpread * 0.55;
          const deficit = Math.abs(desired - targetOrder);
          if (deficit <= 0) continue;
          const push = Math.min(siblingSpread * 0.45, deficit) * crossingStrength * alpha;
          const aDir = Math.sign(sourceOrder);
          branchNodes(a.target).forEach(n => { n.vx += aDir * push; });
          branchNodes(b.target).forEach(n => { n.vx -= aDir * push; });
        }
      }
    };
  }

  function forceLinkCardAvoidance(nodes, linkData) {
    const margin = LAYOUT.linkCardAvoidanceMargin != null ? LAYOUT.linkCardAvoidanceMargin : 0;
    const strength = LAYOUT.linkCardAvoidanceStrength != null ? LAYOUT.linkCardAvoidanceStrength : 0;
    const samples = Math.max(2, Math.round(LAYOUT.linkCardAvoidanceSamples || 8));
    if (strength <= 0 || margin <= 0) return function noop() {};

    return function force(alpha) {
      _linkRouteOffsets = computeLinkRouteOffsets(linkData);
      const polylines = linkData
        .filter(link => link.source && link.target && link.source.project_id === link.target.project_id)
        .map(link => ({ link, pts: linkPolyline(link, samples) }))
        .filter(item => item.pts.length >= 2);

      nodes.forEach(card => {
        if (card.fx != null || card.fy != null) return;
        const threshold = cardRadius(card) + margin;
        const cx = card.x || 0;
        const cy = card.y || 0;
        polylines.forEach(({ link, pts }) => {
          if (link.source.xid === card.xid || link.target.xid === card.xid) return;
          if (link.source.project_id !== card.project_id) return;
          for (let i = 0; i < pts.length - 1; i++) {
            const closest = closestPointOnSegment(cx, cy, pts[i][0], pts[i][1], pts[i + 1][0], pts[i + 1][1]);
            const dx = cx - closest.x;
            const dy = cy - closest.y;
            const dist = Math.hypot(dx, dy);
            if (dist >= threshold) continue;
            const overlap = threshold - dist;
            let ux = 0, uy = 0;
            if (dist > 0.001) {
              ux = dx / dist;
              uy = dy / dist;
            } else {
              const sx = pts[i + 1][0] - pts[i][0];
              const sy = pts[i + 1][1] - pts[i][1];
              const sl = Math.hypot(sx, sy) || 1;
              ux = -sy / sl;
              uy = sx / sl;
            }
            const push = overlap * strength * alpha;
            card.vx += ux * push;
            card.vy += uy * push;
          }
        });
      });
    };
  }

  function closestPointOnSegment(px, py, x1, y1, x2, y2) {
    const dx = x2 - x1;
    const dy = y2 - y1;
    const len2 = dx * dx + dy * dy;
    if (!len2) return { x: x1, y: y1 };
    const t = Math.max(0, Math.min(1, ((px - x1) * dx + (py - y1) * dy) / len2));
    return { x: x1 + dx * t, y: y1 + dy * t };
  }

  function forceCluster(nodes) {
    const strength = LAYOUT.clusterStrength;
    return function force(alpha) {
      const centroids = {}, counts = {};
      nodes.forEach(n => {
        if (n.fx != null) return;  // exclude pinned/dragged nodes from centroid
        const pid = n.project_id;
        if (!centroids[pid]) { centroids[pid] = { x: 0, y: 0 }; counts[pid] = 0; }
        centroids[pid].x += n.x || 0;
        centroids[pid].y += n.y || 0;
        counts[pid]++;
      });
      Object.keys(centroids).forEach(pid => {
        centroids[pid].x /= counts[pid];
        centroids[pid].y /= counts[pid];
      });
      nodes.forEach(n => {
        if (n.fx != null) return;  // don't push pinned nodes
        const c = centroids[n.project_id];
        if (!c) return;
        n.vx += (c.x - (n.x || 0)) * strength * alpha;
        n.vy += (c.y - (n.y || 0)) * strength * alpha;
      });
    };
  }

  function forceProjectCollide(nodes) {
    const padding = LAYOUT.projectCirclePadding;
    const strength = LAYOUT.projectCircleStrength;
    return function force(alpha) {
      const groups = projectBounds(nodes);
      for (let i = 0; i < groups.length; i++) {
        for (let j = i + 1; j < groups.length; j++) {
          const a = groups[i], b = groups[j];
          let dx = b.x - a.x, dy = b.y - a.y;
          let dist = Math.hypot(dx, dy);
          const minDist = a.r + b.r + padding;
          if (dist >= minDist) continue;
          if (!dist) {
            dx = 1;
            dy = 0;
            dist = 1;
          }
          const push = (minDist - dist) / dist * strength * alpha;
          const ax = dx * push * 0.5, ay = dy * push * 0.5;
          a.nodes.forEach(n => { n.vx -= ax; n.vy -= ay; });
          b.nodes.forEach(n => { n.vx += ax; n.vy += ay; });
        }
      }
    };
  }

  function forceProjectBounds(nodes) {
    const strength = LAYOUT.foreignCardBoundaryStrength;
    const margin = LAYOUT.hullPad + LAYOUT.foreignCardBoundaryGap;
    return function force(alpha) {
      const bounds = projectRects(nodes, margin);
      nodes.forEach(n => {
        const x = n.x || 0, y = n.y || 0;
        bounds.forEach(b => {
          if (b.pid === n.project_id) return;
          if (x < b.x0 || x > b.x1 || y < b.y0 || y > b.y1) return;

          const left = x - b.x0;
          const right = b.x1 - x;
          const top = y - b.y0;
          const bottom = b.y1 - y;
          const min = Math.min(left, right, top, bottom);
          const push = (margin - Math.max(0, min)) * strength * alpha;
          if (min === left) n.vx += push;
          else if (min === right) n.vx -= push;
          else if (min === top) n.vy += push;
          else n.vy -= push;
        });
      });
    };
  }

  function forceProjectRectCollide(nodes) {
    const gap = LAYOUT.projectRectGap;
    const strength = LAYOUT.projectRectStrength;
    return function force(alpha) {
      const rects = projectRects(nodes, LAYOUT.hullPad);
      for (let i = 0; i < rects.length; i++) {
        for (let j = i + 1; j < rects.length; j++) {
          const a = rects[i], b = rects[j];
          const dx = b.cx - a.cx;
          const dy = b.cy - a.cy;
          const overlapX = (a.w + b.w) / 2 + gap - Math.abs(dx);
          const overlapY = (a.h + b.h) / 2 + gap - Math.abs(dy);
          if (overlapX <= 0 || overlapY <= 0) continue;

          if (overlapX < overlapY) {
            const dir = dx < 0 ? -1 : 1;
            const push = overlapX * strength * alpha * 0.5;
            a.nodes.forEach(n => { n.vx -= dir * push; });
            b.nodes.forEach(n => { n.vx += dir * push; });
          } else {
            const dir = dy < 0 ? -1 : 1;
            const push = overlapY * strength * alpha * 0.5;
            a.nodes.forEach(n => { n.vy -= dir * push; });
            b.nodes.forEach(n => { n.vy += dir * push; });
          }
        }
      }
    };
  }

  function projectBounds(nodes) {
    const byProject = {};
    nodes.forEach(n => {
      if (!n.is_alive) return;
      (byProject[n.project_id] = byProject[n.project_id] || []).push(n);
    });
    return Object.entries(byProject).map(([pid, members]) => {
      const x = d3.mean(members, n => n.x || 0) || 0;
      const y = d3.mean(members, n => n.y || 0) || 0;
      const r = d3.max(members, n => Math.hypot((n.x || 0) - x, (n.y || 0) - y) + cardRadius(n)) || 0;
      return { pid, nodes: members, x, y, r };
    });
  }

  function projectRects(nodes, margin) {
    const byProject = {};
    nodes.forEach(n => {
      if (!n.is_alive) return;
      (byProject[n.project_id] = byProject[n.project_id] || []).push(n);
    });
    return Object.entries(byProject).map(([pid, members]) => {
      const x0 = d3.min(members, n => (n.x || 0) - cardSize(n).w / 2 - margin);
      const x1 = d3.max(members, n => (n.x || 0) + cardSize(n).w / 2 + margin);
      const y0 = d3.min(members, n => (n.y || 0) - cardSize(n).h / 2 - margin);
      const y1 = d3.max(members, n => (n.y || 0) + cardSize(n).h / 2 + margin);
      return {
        pid, nodes: members, x0, x1, y0, y1,
        cx: (x0 + x1) / 2,
        cy: (y0 + y1) / 2,
        w: x1 - x0,
        h: y1 - y0,
      };
    });
  }

  // ── Drag ──────────────────────────────────────────────────────────────────
  function onKeyDown(e) {
    if (e.key === "Control") {
      _ctrlDown = true;
      setForceFrozen(true);
    }
    if (e.key === "Escape") hideRadialMenu();
  }

  function onKeyUp(e) {
    if (e.key === "Control") {
      _ctrlDown = false;
      setForceFrozen(false);
    }
  }

  function setForceFrozen(frozen) {
    if (_forceFrozen === frozen) return;
    _forceFrozen = frozen;
    if (!_simulation) return;
    if (frozen) {
      _simulation.stop();
      _data.nodes.forEach(n => { n.vx = 0; n.vy = 0; });
      renderNodesOnly();
      renderLinksOnly();
    } else {
      _simulation.alphaTarget(0).alpha(0.08).restart();
    }
  }

  function eventWantsFreeze(e) {
    return _ctrlDown || _forceFrozen || !!(e.sourceEvent && e.sourceEvent.ctrlKey);
  }

  function dragStarted(e, d) {
    _dragActive = true;
    _queuedGraphData = null;
    _dragFreeze = eventWantsFreeze(e);
    if (_simulation) _simulation.stop();
    _dragStart = { x: e.x, y: e.y, project_id: d.project_id, started_at: Date.now() };
    _dragOriginProject = d.project_id;
    _dragOriginHull = null;
    _dragMoved = false;
    _dragTargetProject = null;
    _dragTargetParent = null;
    _dragOriginHull = buildVisualHullDatum(
      _dragOriginProject,
      _data.nodes.filter(n => n.is_alive && n.project_id === _dragOriginProject)
    );
    _dragDropHulls = buildDropHulls(d);
    setDragFeedback(d, null, null);
    d.fx = d.x; d.fy = d.y;
    const hullSummary = _dragDropHulls.map(h => `${h.pid}(rects=${h.rects.length},hull=${!!h.hull})`).join(" ");
    console.log(`[drag start] xid=${d.xid} project=${d.project_id} origin=${_dragOriginProject} hulls: ${hullSummary}`);
  }
  function dragged(e, d) {
    if (eventWantsFreeze(e)) _dragFreeze = true;
    if (_simulation) _simulation.stop();
    const moved = _dragStart ? Math.hypot(e.x - _dragStart.x, e.y - _dragStart.y) : 0;
    if (!_dragMoved && moved >= LAYOUT.clickMoveTolerancePx) _dragMoved = true;
    d.fx = e.x; d.fy = e.y;
    d.x = e.x; d.y = e.y;
    _dragTargetProject = findDropProject(e.x, e.y, d);
    _dragTargetParent = findDropParent(e.x, e.y, d, _dragTargetProject);
    if (_dragTargetParent) _dragTargetProject = _dragTargetParent.project_id;

    // Detect drag over cluster label using getBoundingClientRect — works correctly
    // with CSS matrix3d transforms on the SVG group (elementFromPoint does not).
    const px = e.sourceEvent.clientX, py = e.sourceEvent.clientY;
    let foundLabel = null;
    _g.select(".labels-layer").selectAll(".cluster-label").each(function(ld) {
      const r = this.getBoundingClientRect();
      const padX = 28, padY = 18;
      if (px >= r.left - padX && px <= r.right + padX &&
          py >= r.top - padY && py <= r.bottom + padY) {
        foundLabel = ld;
      }
    });
    _dragTargetLabel = foundLabel ? { pid: foundLabel.pid, name: foundLabel.proj.name } : null;
    d3.selectAll(".cluster-label").classed("label-drop-target", false);
    if (_dragTargetLabel) {
      d3.selectAll(".cluster-label").filter(ld => ld.pid === _dragTargetLabel.pid)
        .classed("label-drop-target", true);
    }

    setDragFeedback(d, _dragTargetProject, _dragTargetParent);
    renderNodesOnly();
    renderLinksOnly();
    renderHulls();
  }
  function dragEnded(e, d) {
    const moved = _dragStart ? Math.hypot(e.x - _dragStart.x, e.y - _dragStart.y) : 0;
    const heldMs = _dragStart ? Date.now() - (_dragStart.started_at || Date.now()) : 0;
    const restore = _dragStart;
    const startedProject = _dragStart ? _dragStart.project_id : d.project_id;
    _dragStart = null;
    _dragMoved = false;
    _dragFreeze = false;
    const keepFrozen = _ctrlDown || !!(e.sourceEvent && e.sourceEvent.ctrlKey);
    if (!keepFrozen) setForceFrozen(false);
    if (!e.active) _simulation.alphaTarget(0);
    d.fx = null; d.fy = null;
    d.vx = 0; d.vy = 0;
    if (moved < LAYOUT.clickMoveTolerancePx) {
      if (restore) { d.x = restore.x; d.y = restore.y; }
      stopLayoutMotion();
      renderNodesOnly();
      renderLinksOnly();
      clearDragFeedback();
      _dragDropHulls = null;
      _dragActive = false;
      _dragOriginProject = null;
      _dragOriginHull = null;
      _dragTargetProject = null;
      _dragTargetParent = null;
      flushQueuedGraphData();
      if (heldMs <= LAYOUT.clickHoldTimeoutMs) {
        d._activatedFromDragEnd = true;
        onNodeClick(d, `drag-click ${heldMs}ms ${moved.toFixed(1)}px`);
      } else {
        showClickDiscarded(d, `drag-hold ${heldMs}ms ${moved.toFixed(1)}px`);
      }
      return;
    }

    const parentTarget = _dragTargetParent;
    const target = _dragTargetProject;
    const ownProject = soloProjectId(d.xid, startedProject);
    let nextProject = target || ownProject;
    if (target === startedProject && isRecentDetach(d.xid, startedProject)) {
      nextProject = ownProject;
      console.log("ignored stale reinsertion", d.xid, startedProject);
    }
    console.log(`[drag end] xid=${d.xid} from=${startedProject} target=${target} parent=${parentTarget ? parentTarget.xid : null} next=${nextProject} own=${ownProject} changed=${nextProject !== d.project_id}`);
    const projectChanged = nextProject !== d.project_id;
    const labelTarget = _dragTargetLabel;
    _dragTargetLabel = null;
    d3.selectAll(".cluster-label").classed("label-drop-target", false);

    if (labelTarget) {
      if (restore) { d.x = restore.x; d.y = restore.y; }
      d.fx = null; d.fy = null;
      stopLayoutMotion();
      renderNodesOnly();
      renderLinksOnly();
      clearDragFeedback();
      _dragDropHulls = null;
      _dragActive = false;
      _dragOriginProject = null;
      _dragOriginHull = null;
      _dragTargetProject = null;
      _dragTargetParent = null;
      _queuedGraphData = null;
      sendToBackend({ action: "rename_project", project_id: labelTarget.pid, name: d.app_name || d.title });
      return;
    } else if (parentTarget) {
      d.parent_xid = parentTarget.xid;
      _projectAnchors = computeProjectAnchors(_data.nodes.filter(n => n.is_alive));
      sendToBackend({ action: "set_parent", xid: d.xid, parent_xid: parentTarget.xid, with_children: "same_project" });
    } else if (projectChanged) {
      if (nextProject === ownProject && startedProject !== ownProject) {
        _recentDetach[d.xid] = { from: startedProject, until: Date.now() + 6000 };
      }
      _projectAnchors = computeProjectAnchors(_data.nodes.filter(n => n.is_alive));
      sendToBackend({ action: "move_node", xid: d.xid, project_id: nextProject, with_children: "same_project" });
    }

    clearDragFeedback();
    _dragDropHulls = null;
    _dragActive = false;
    _dragOriginProject = null;
    _dragOriginHull = null;
    _dragTargetProject = null;
    _dragTargetParent = null;
    renderHulls();
    if (projectChanged || parentTarget) {
      _queuedGraphData = null;
      _settleAfterMoveUntil = Date.now() + 2500;
    }
    else flushQueuedGraphData();
    if (!keepFrozen && !_forceFrozen) restartLayout((projectChanged || parentTarget) ? 0.65 : 0.08);
  }

  function restartLayout(alpha) {
    if (!_simulation) return;
    _simulation
      .alphaTarget(0)
      .alpha(Math.max(_simulation.alpha(), alpha))
      .restart();
  }

  function flushQueuedGraphData() {
    if (!_queuedGraphData) return;
    const data = _queuedGraphData;
    _queuedGraphData = null;
    window.windMgr.updateGraph(data);
  }

  function isRecentDetach(xid, projectId) {
    const rec = _recentDetach[xid];
    if (!rec) return false;
    if (Date.now() > rec.until) {
      delete _recentDetach[xid];
      return false;
    }
    return rec.from === projectId;
  }

  function soloProjectId(xid, currentProjectId) {
    const base = `${xid}:solo`;
    const memberCount = _data.nodes.filter(n => n.is_alive && n.project_id === base).length;
    if (currentProjectId === base && memberCount > 1) {
      return `${xid}:solo:${Date.now()}`;
    }
    return base;
  }

  function findDropProject(x, y, dragged) {
    const containing = findProjectContainingPoint(x, y, dragged);
    if (containing) return containing;
    if (!LAYOUT.dropNearestDistance || LAYOUT.dropNearestDistance <= 0) return null;
    return findNearestProject(x, y, dragged.project_id, LAYOUT.dropNearestDistance);
  }

  function findDropParent(x, y, dragged, targetProjectId) {
    let best = null;
    let bestArea = Infinity;
    _data.nodes.forEach(n => {
      if (!n.is_alive || n.xid === dragged.xid) return;
      if (!targetProjectId || n.project_id !== targetProjectId) return;
      if (wouldCreateParentCycle(dragged.xid, n.xid) && !isDescendantOf(dragged.xid, n.xid)) return;
      const size = cardSize(n);
      const pad = LAYOUT.dropParentInnerPad != null ? LAYOUT.dropParentInnerPad : -18;
      const x0 = (n.x || 0) - size.w / 2 - pad;
      const x1 = (n.x || 0) + size.w / 2 + pad;
      const y0 = (n.y || 0) - size.h / 2 - pad;
      const y1 = (n.y || 0) + size.h / 2 + pad;
      if (x < x0 || x > x1 || y < y0 || y > y1) return;
      const area = (x1 - x0) * (y1 - y0);
      if (area < bestArea) {
        best = n;
        bestArea = area;
      }
    });
    return best;
  }

  function wouldCreateParentCycle(childXid, candidateParentXid) {
    let current = _nodeMap[candidateParentXid];
    const seen = new Set();
    while (current && current.parent_xid != null) {
      if (current.xid === childXid) return true;
      if (seen.has(current.xid)) return true;
      seen.add(current.xid);
      current = _nodeMap[current.parent_xid];
    }
    return current && current.xid === childXid;
  }

  function isDescendantOf(rootXid, candidateXid) {
    const stack = _data.nodes
      .filter(n => n.is_alive && n.parent_xid === rootXid)
      .map(n => n.xid);
    const seen = new Set();
    while (stack.length) {
      const xid = stack.pop();
      if (xid === candidateXid) return true;
      if (seen.has(xid)) continue;
      seen.add(xid);
      _data.nodes.forEach(n => {
        if (n.is_alive && n.parent_xid === xid) stack.push(n.xid);
      });
    }
    return false;
  }

  function findProjectContainingPoint(x, y, dragged) {
    const hulls = _dragDropHulls || buildDropHulls(dragged);
    for (const h of hulls) {
      if (h.rects && h.rects.some(r => x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1)) {
        return h.pid;
      }
      if (h.hull && d3.polygonContains(h.hull, [x, y])) return h.pid;
    }
    return null;
  }

  function buildDropHulls(dragged) {
    return _data.projects.map(p => {
      const members = _data.nodes.filter(n =>
        n.is_alive && n.project_id === p.id);
      if (!members.length) return { pid: p.id, hull: null, rects: [] };
      const pad = LAYOUT.dropHullPad;
      const pts = members.flatMap(n => [
        [(n.x || 0) - cardSize(n).w / 2 - pad, (n.y || 0) - cardSize(n).h / 2 - pad],
        [(n.x || 0) + cardSize(n).w / 2 + pad, (n.y || 0) - cardSize(n).h / 2 - pad],
        [(n.x || 0) + cardSize(n).w / 2 + pad, (n.y || 0) + cardSize(n).h / 2 + pad],
        [(n.x || 0) - cardSize(n).w / 2 - pad, (n.y || 0) + cardSize(n).h / 2 + pad],
      ]);
      const rectPad = LAYOUT.dropIntoPad;
      const rects = members.map(n => ({
        x0: (n.x || 0) - cardSize(n).w / 2 - rectPad,
        x1: (n.x || 0) + cardSize(n).w / 2 + rectPad,
        y0: (n.y || 0) - cardSize(n).h / 2 - rectPad,
        y1: (n.y || 0) + cardSize(n).h / 2 + rectPad,
      }));
      return { pid: p.id, hull: d3.polygonHull(pts), rects };
    });
  }

  function setDragFeedback(d, targetProjectId, targetParent) {
    const node = d3.select(`[data-xid="${d.xid}"]`);
    node.classed("dragging", true)
      .classed("drop-into", !!targetProjectId || !!targetParent)
      .classed("drop-out", !targetProjectId);

    _g.select(".hulls-layer").selectAll(".hull-group")
      .classed("drop-target", h => !!targetProjectId && h.pid === targetProjectId);
    d3.selectAll(".node-g")
      .classed("drop-parent-target", n => !!targetParent && n.xid === targetParent.xid);
  }

  function clearDragFeedback() {
    d3.selectAll(".node-g")
      .classed("dragging", false)
      .classed("drop-into", false)
      .classed("drop-out", false);
    _g.select(".hulls-layer").selectAll(".hull-group")
      .classed("drop-target", false);
    d3.selectAll(".node-g")
      .classed("drop-parent-target", false);
    d3.selectAll(".cluster-label")
      .classed("label-drop-target", false);
  }

  function findNearestProject(x, y, currentPid, threshold) {
    let best = null, bestDist = threshold;
    _data.projects.forEach(p => {
      if (p.id === currentPid) return;
      const members = _data.nodes.filter(n => n.is_alive && n.project_id === p.id);
      if (!members.length) return;
      const cx = d3.mean(members, n => n.x), cy = d3.mean(members, n => n.y);
      const dist = Math.hypot(x - cx, y - cy);
      if (dist < bestDist) { bestDist = dist; best = p.id; }
    });
    return best;
  }

  // ── Interactions ──────────────────────────────────────────────────────────
  function onNodePointerDown(e, d) {
    if (e.button !== 0) return;
    d._pendingClick = {
      x: e.clientX,
      y: e.clientY,
      started_at: Date.now(),
      handled: false,
    };
    const g = d3.select(`[data-xid="${d.xid}"]`);
    g.classed("click-pending", true)
      .classed("click-discarded", false)
      .classed("click-activate", false);
    d._pendingClickTimer = setTimeout(() => {
      if (!d._pendingClick || d._pendingClick.handled) return;
      showClickDiscarded(d, `hold-timeout ${LAYOUT.clickHoldTimeoutMs}ms`);
    }, LAYOUT.clickHoldTimeoutMs);
  }

  function onNodePointerUp(e, d) {
    if (e.button !== 0 || !d._pendingClick) return;
    const press = d._pendingClick;
    const heldMs = Date.now() - press.started_at;
    const moved = Math.hypot(e.clientX - press.x, e.clientY - press.y);
    if (heldMs <= LAYOUT.clickHoldTimeoutMs && moved < LAYOUT.clickMoveTolerancePx) {
      press.handled = true;
      d._activatedFromPointer = true;
      onNodeClick(d, `pointer-click ${heldMs}ms ${moved.toFixed(1)}px`);
      clearPendingClick(d);
      return;
    }
    showClickDiscarded(d, `pointer-discard ${heldMs}ms ${moved.toFixed(1)}px`);
  }

  function clearPendingClick(d) {
    if (d._pendingClickTimer) {
      clearTimeout(d._pendingClickTimer);
      d._pendingClickTimer = null;
    }
    d._pendingClick = null;
    d3.select(`[data-xid="${d.xid}"]`).classed("click-pending", false);
  }

  function discardPendingClick(d, reason) {
    if (!d._pendingClick) return;
    showClickDiscarded(d, reason);
  }

  function showClickDiscarded(d, reason) {
    if (d._pendingClick) d._pendingClick.handled = true;
    clearPendingClick(d);
    d._discardClickUntil = Date.now() + 350;
    console.log(`[click-discard] xid=${d.xid} reason=${reason}`);
    flashNodeClass(d.xid, "click-discarded", 350);
  }

  function flashNodeClass(xid, className, ms) {
    const g = d3.select(`[data-xid="${xid}"]`);
    g.classed(className, false);
    requestAnimationFrame(() => {
      g.classed(className, true);
      setTimeout(() => g.classed(className, false), ms);
    });
  }

  function onNodeClick(d, source = "click") {
    const now = Date.now();
    if (d._lastActivateSentAt && now - d._lastActivateSentAt < 180) {
      return;
    }
    d._lastActivateSentAt = now;
    clearPendingClick(d);
    flashNodeClass(d.xid, "click-activate", 260);
    stopLayoutMotion();
    _selectedXid = d.xid;
    d3.selectAll(".node-g").classed("selected", false);
    d3.select(`[data-xid="${d.xid}"]`).classed("selected", true);
    console.log(`[activate-send] xid=${d.xid} source=${source}`);
    sendToBackend({ action: "activate", xid: d.xid });
  }

  function onNodeHover(d) {
    _hoveredNode = d;
    _livePreviewNode = d;
    applyActiveWindowClasses();
    const now = Date.now();
    const isStatsDue = now - (_lastHoverSentAt[d.xid] || 0) >= 2000;
    if (isStatsDue) _lastHoverSentAt[d.xid] = now;
    sendLivePreviewBounds(d, isStatsDue ? "card_hover" : "live_preview_update");
  }

  function onNodeLeave(d) {
    if (_hoveredNode && _hoveredNode.xid === d.xid) _hoveredNode = null;
    applyActiveWindowClasses();
    sendToBackend({ action: "card_hover_leave", xid: d.xid });
    sendToBackend({ action: "live_preview_idle", xid: d.xid });
  }

  function sendLivePreviewBoundsThrottled() {
    if (!_livePreviewNode) return;
    const now = Date.now();
    if (now - _lastLivePreviewSentAt < 80) return;
    const action = (_hoveredNode && _hoveredNode.xid === _livePreviewNode.xid)
      ? "live_preview_update"
      : "live_preview_bounds";
    sendLivePreviewBounds(_livePreviewNode, action);
  }

  function sendLivePreviewBounds(d, action) {
    const el = document.querySelector(`[data-xid="${d.xid}"]`);
    if (!el) {
      if (_livePreviewNode && _livePreviewNode.xid === d.xid) _livePreviewNode = null;
      sendToBackend({ action: "live_preview_hide", xid: d.xid });
      return;
    }
    const rect = el.getBoundingClientRect();
    if (rect.width <= 4 || rect.height <= 4) return;
    _lastLivePreviewSentAt = Date.now();
    sendToBackend({
      action,
      xid: d.xid,
      viewport_x: rect.left,
      viewport_y: rect.top,
      width: rect.width,
      height: rect.height,
    });
  }

  function stopLayoutMotion() {
    if (!_simulation) return;
    _simulation.stop();
    _simulation.alphaTarget(0);
    _data.nodes.forEach(n => { n.vx = 0; n.vy = 0; });
  }

  function toggleProject(pid) {
    sendToBackend({ action: "toggle_project", project_id: pid });
  }

  function resetLayout() {
    if (!_data) return;
    const alive = _data.nodes.filter(n => n.is_alive);
    _projectAnchors = computeProjectAnchors(alive);
    const xidToNode = {};
    alive.forEach(n => { xidToNode[n.xid] = n; });
    const edges = _data.edges.filter(e => {
      const s = xidToNode[e.source], t = xidToNode[e.target];
      return s && t && s.is_alive && t.is_alive;
    });

    // Pre-place each node near its project anchor so render()'s position-
    // preservation code picks up fresh coordinates instead of stale ones,
    // and forces don't need to drag cards all the way from (0,0).
    _g.select(".nodes-layer").selectAll(".node-g").each(function (d) {
      const a = _projectAnchors[d.project_id] || {};
      d.x = (a.x || window.innerWidth / 2)  + (Math.random() - 0.5) * 80;
      d.y = (a.y || window.innerHeight / 2) + (Math.random() - 0.5) * 80;
      d.vx = 0; d.vy = 0;
    });
    if (LAYOUT.hierarchyPrelayoutEnabled !== false) {
      applyHierarchyPrelayout(alive, edges, xidToNode);
      _prelayoutInitializedProjects = new Set(alive.map(n => n.project_id));
    }

    render(true);   // simulation starts from anchor positions
    fitView(false); // frame those positions immediately; no startup transition race
  }

  function fitView(animate = true) {
    const nodes = _data.nodes.filter(n => n.is_alive);
    if (!nodes.length) return;
    const xs = nodes.map(n => n.x), ys = nodes.map(n => n.y);
    const x0 = Math.min(...xs) - LAYOUT.fitMarginLeft;
    const y0 = Math.min(...ys) - LAYOUT.fitMarginTop;
    const x1 = Math.max(...xs) + LAYOUT.fitMarginRight;
    const y1 = Math.max(...ys) + LAYOUT.fitMarginBottom;
    const W = window.innerWidth, H = window.innerHeight;
    const scale = Math.min(0.9, Math.min(W / (x1 - x0), H / (y1 - y0)));
    const tx = (W - scale * (x0 + x1)) / 2;
    const ty = (H - scale * (y0 + y1)) / 2;
    _viewCenterTarget = { x: (x0 + x1) / 2, y: (y0 + y1) / 2 };
    const transform = d3.zoomIdentity.translate(tx, ty).scale(scale);
    if (!animate) {
      applyZoomImmediately(transform);
      return;
    }
    _svg.transition().duration(600)
      .call(_zoom.transform, transform)
      .on("end", () => {
        _currentZoomTransform = transform;
        applyZoomTransform(transform);
      });
  }

  function ensureInitialViewCenterTarget(nodes) {
    if (_viewCenterTarget || !nodes.length) return;
    const xs = nodes.map(n => n.x).filter(Number.isFinite);
    const ys = nodes.map(n => n.y).filter(Number.isFinite);
    if (!xs.length || !ys.length) return;
    _viewCenterTarget = {
      x: (Math.min(...xs) + Math.max(...xs)) / 2,
      y: (Math.min(...ys) + Math.max(...ys)) / 2,
    };
    console.log(
      `[center-init] target=(${_viewCenterTarget.x.toFixed(1)},${_viewCenterTarget.y.toFixed(1)}) ` +
      `bounds=(${Math.min(...xs).toFixed(1)},${Math.min(...ys).toFixed(1)})-(${Math.max(...xs).toFixed(1)},${Math.max(...ys).toFixed(1)}) ` +
      `viewport=${window.innerWidth}x${window.innerHeight}`
    );
  }

  function rememberViewCenterTarget() {
    if (!_currentZoomTransform) return;
    const k = _currentZoomTransform.k || 1;
    _viewCenterTarget = {
      x: (window.innerWidth / 2 - _currentZoomTransform.x) / k,
      y: (window.innerHeight / 2 - _currentZoomTransform.y) / k,
    };
    console.log(
      `[center-remember] target=(${_viewCenterTarget.x.toFixed(1)},${_viewCenterTarget.y.toFixed(1)}) ` +
      `viewport=${window.innerWidth}x${window.innerHeight} ` +
      `zoom=k${k.toFixed(4)} tx=${_currentZoomTransform.x.toFixed(1)} ty=${_currentZoomTransform.y.toFixed(1)}`
    );
  }

  function centerRememberedView() {
    if (!_svg || !_zoom) return;
    if (!_viewCenterTarget) rememberViewCenterTarget();
    if (!_viewCenterTarget) return;
    const k = (_currentZoomTransform && _currentZoomTransform.k) || 1;
    const tx = window.innerWidth / 2 - k * _viewCenterTarget.x;
    const ty = window.innerHeight / 2 - k * _viewCenterTarget.y;
    const transform = d3.zoomIdentity.translate(tx, ty).scale(k);
    console.log(
      `[center-apply] target=(${_viewCenterTarget.x.toFixed(1)},${_viewCenterTarget.y.toFixed(1)}) ` +
      `viewport=${window.innerWidth}x${window.innerHeight} ` +
      `zoom=k${k.toFixed(4)} tx=${tx.toFixed(1)} ty=${ty.toFixed(1)}`
    );
    applyZoomImmediately(transform);
  }

  function applyZoomImmediately(transform) {
    _svg.interrupt();
    _svg.call(_zoom.transform, transform);
    _currentZoomTransform = transform;
    applyZoomTransform(transform);
  }

  // ── Context menu ──────────────────────────────────────────────────────────
  let _ctxNode = null;

  function showContextMenu(e, d) {
    _ctxNode = d;
    const menu = document.getElementById("context-menu");
    e.stopPropagation();

    populatePlacementMenu(d);
    populateMonitorMenu(d);

    const projectList = document.getElementById("ctx-project-list");
    projectList.innerHTML = "";
    let projectItemCount = 0;
    _data.projects.forEach(p => {
      if (p.id === d.project_id) return;
      const item = document.createElement("div");
      item.className = "menu-item";
      item.textContent = "→ " + p.name;
      item.onclick = () => {
        sendToBackend({ action: "move_node", xid: d.xid, project_id: p.id, with_children: true });
        hideContextMenu();
      };
      projectList.appendChild(item);
      projectItemCount += 1;
    });
    if (projectItemCount === 0) {
      const item = document.createElement("div");
      item.className = "menu-item disabled";
      item.textContent = "No other projects";
      projectList.appendChild(item);
    }

    const resetNameItem = document.getElementById("ctx-reset-card-name");
    if (resetNameItem) resetNameItem.style.display = d.card_name ? "" : "none";

    menu.style.display = "block";
    menu.style.left = "0px";
    menu.style.top = "0px";
    const rect = menu.getBoundingClientRect();
    const pad = 8;
    menu.style.left = Math.max(pad, Math.min(e.clientX, window.innerWidth - rect.width - pad)) + "px";
    menu.style.top = Math.max(pad, Math.min(e.clientY, window.innerHeight - rect.height - pad)) + "px";
    placeProjectSubmenu();
  }

  function populateMonitorMenu(d) {
    const list = document.getElementById("ctx-monitor-list");
    if (!list) return;
    list.innerHTML = "";
    [
      [0, "Monitor 1"],
      [1, "Monitor 2"],
    ].forEach(([monitorIndex, label]) => {
      const item = document.createElement("div");
      item.className = "menu-item";
      item.textContent = label;
      item.onclick = () => {
        sendToBackend({ action: "move_window_monitor", xid: d.xid, monitor_index: monitorIndex });
        hideContextMenu();
      };
      list.appendChild(item);
    });
  }

  function placeProjectSubmenu() {
    const trigger = document.getElementById("ctx-move-project-submenu");
    const panel = document.getElementById("ctx-project-list");
    if (!trigger || !panel) return;
    const triggerRect = trigger.getBoundingClientRect();
    const pad = 8;
    panel.style.left = "0px";
    panel.style.top = "0px";
    panel.style.display = "block";
    const panelRect = panel.getBoundingClientRect();
    panel.style.display = "";
    const opensLeft = triggerRect.right + panelRect.width + pad > window.innerWidth;
    const left = opensLeft
      ? Math.max(pad, triggerRect.left - panelRect.width)
      : Math.min(triggerRect.right, window.innerWidth - panelRect.width - pad);
    const top = Math.max(pad, Math.min(triggerRect.top, window.innerHeight - panelRect.height - pad));
    panel.style.left = left + "px";
    panel.style.top = top + "px";
  }

  function populatePlacementMenu(d) {
    const list = document.getElementById("ctx-placement-list");
    list.innerHTML = "";
    const rawW = Number(d.window_width) || 0;
    const rawH = Number(d.window_height) || 0;
    const orientation = String(d.monitor_orientation || "").toLowerCase();
    const vertical = orientation === "vertical" || (orientation !== "horizontal" && rawH > rawW);
    const items = vertical
      ? [
          ["maximize", "Maximize"],
          ["top_half", "1/2"],
          ["bottom_half", "2/2"],
          ["top_third", "1/3"],
          ["middle_third", "2/3"],
          ["bottom_third", "3/3"],
        ]
      : [
          ["maximize", "Maximize"],
          ["right_half", "__|##"],
          ["left_half", "##|__"],
          ["right_third", "__|__|##"],
          ["middle_third", "__|##|__"],
          ["left_third", "##|__|__"],
        ];
    items.forEach(([placement, label]) => {
      const item = document.createElement("div");
      item.className = "menu-item";
      item.textContent = label;
      item.onclick = () => {
        sendToBackend({ action: "place_window", xid: d.xid, placement });
        hideContextMenu();
      };
      list.appendChild(item);
    });
  }

  function hideContextMenu() {
    document.getElementById("context-menu").style.display = "none";
    _ctxNode = null;
  }

  let _ctxLink = null;
  function showLinkContextMenu(e, d) {
    hideContextMenu();
    _ctxLink = d;
    const menu = document.getElementById("link-context-menu");
    menu.style.display = "block";
    menu.style.left = "0px";
    menu.style.top = "0px";
    const rect = menu.getBoundingClientRect();
    const pad = 8;
    menu.style.left = Math.max(pad, Math.min(e.clientX, window.innerWidth - rect.width - pad)) + "px";
    menu.style.top = Math.max(pad, Math.min(e.clientY, window.innerHeight - rect.height - pad)) + "px";
  }

  function hideLinkContextMenu() {
    document.getElementById("link-context-menu").style.display = "none";
    _ctxLink = null;
  }

  function ctxLinkAction(action) {
    if (!_ctxLink) return;
    if (action === "remove_link") {
      sendToBackend({ action: "remove_link", xid: _ctxLink.target.xid });
    }
    hideLinkContextMenu();
  }

  function ctxAction(action) {
    if (!_ctxNode) return;
    if (action === "activate") {
      sendToBackend({ action: "activate", xid: _ctxNode.xid });
    } else if (action.startsWith("place_")) {
      sendToBackend({ action: "place_window", xid: _ctxNode.xid, placement: action.slice("place_".length) });
    } else if (action === "refresh_thumb") {
      sendToBackend({ action: "refresh_thumb", xid: _ctxNode.xid });
    } else if (action === "detach") {
      sendToBackend({ action: "remove_link", xid: _ctxNode.xid });
    } else if (action === "unlink_children") {
      sendToBackend({ action: "unlink_children", xid: _ctxNode.xid });
    } else if (action === "reset_card_name") {
      sendToBackend({ action: "set_card_name", xid: _ctxNode.xid, name: "" });
    }
    hideContextMenu();
  }

  function renameProject(projectId) {
    const current = _projectMap[projectId]?.name || "";
    const name = prompt("New project name:", current);
    if (name && name !== current) {
      sendToBackend({ action: "rename_project", project_id: projectId, name });
    }
  }

  // ── Status ────────────────────────────────────────────────────────────────
  function updateStatus() {
    const alive = _data.nodes.filter(n => n.is_alive).length;
    document.getElementById("status-text").textContent =
      `${alive} windows · ${_data.projects.length} projects`;
  }

  // ── Backend bridge ────────────────────────────────────────────────────────
  function sendToBackend(msg) {
    try { window.webkit.messageHandlers.api.postMessage(msg); }
    catch(e) { console.log("[wind_mgr] backend msg:", JSON.stringify(msg)); }
  }

  // ── Helpers ───────────────────────────────────────────────────────────────
  function appEmoji(appType) {
    return ({ chrome:"🌐", vscode:"💻", editor:"📝", terminal:"⬛", generic:"🪟" })[appType] || "🪟";
  }

  // ── Radial app launcher ───────────────────────────────────────────────────

  const _INNER_R_MIN = 90; // px — minimum favorites ring radius
  const _INNER_SPACING = 52; // px — min center-to-center for inner ring icons
  const _RING_STEP = 95;   // px — gap between outer rings

  // How many items fit on a ring given its radius and icon spacing
  function _ringCapacity(r, isInner) {
    const spacing = isInner ? _INNER_SPACING : 44; // min center-to-center px
    return Math.max(1, Math.floor(2 * Math.PI * r / spacing));
  }

  // Radius that fits exactly n items with _INNER_SPACING between them
  function _innerRadiusFor(n) {
    if (n <= 1) return _INNER_R_MIN;
    return Math.max(_INNER_R_MIN, Math.ceil(n * _INNER_SPACING / (2 * Math.PI)));
  }

  let _radialOpen = false;
  let _radialGraphPos = null;   // {x, y} in graph coords
  let _radialContext = null;    // {project_id, parent_xid} or null (own group)

  function showRadialMenu(screenX, screenY, graphX, graphY, context) {
    if (!_appList.length) return;
    _radialGraphPos = { x: graphX, y: graphY };
    _radialContext = context || null;

    const favs = _appList.filter(a => a.favorite);
    const others = _appList.filter(a => !a.favorite);

    // Inner ring: expand radius to fit all favorites rather than clipping them
    const innerR = _innerRadiusFor(favs.length);
    const innerItems = favs;
    let remaining = [...others];

    const outerRings = []; // [{r, items}]
    let ringIdx = 0;
    while (remaining.length > 0) {
      const r = innerR + _RING_STEP * (ringIdx + 1);
      const cap = _ringCapacity(r, false);
      outerRings.push({ r, items: remaining.splice(0, cap) });
      ringIdx++;
    }

    const outerR = outerRings.length ? outerRings[outerRings.length - 1].r : innerR;

    // Clamp center to viewport so rings don't go off-screen
    const margin = outerR + 24;
    const cx = Math.max(margin, Math.min(window.innerWidth - margin, screenX));
    const cy = Math.max(margin, Math.min(window.innerHeight - margin, screenY));

    const menu = document.getElementById("radial-menu");
    const rings = document.getElementById("radial-rings");
    rings.innerHTML = "";

    // Fader background — radial gradient circle sized to outermost ring
    const faderR = outerR + 48;
    const fader = document.createElement("div");
    fader.className = "radial-fader";
    const fd = faderR * 2;
    fader.style.cssText = `left:${cx}px;top:${cy}px;width:${fd}px;height:${fd}px;`;
    rings.appendChild(fader);

    // Guide circles for each ring
    if (innerItems.length) _addGuideCircle(rings, cx, cy, innerR);
    outerRings.forEach(ring => _addGuideCircle(rings, cx, cy, ring.r));

    // Center dot
    const dot = document.createElement("div");
    dot.className = "radial-center";
    dot.style.left = cx + "px";
    dot.style.top = cy + "px";
    rings.appendChild(dot);

    // Populate rings
    innerItems.forEach((app, i) => {
      _addRadialItem(rings, app, i, innerItems.length, innerR, cx, cy, false);
    });
    outerRings.forEach(ring => {
      ring.items.forEach((app, i) => {
        _addRadialItem(rings, app, i, ring.items.length, ring.r, cx, cy, true);
      });
    });

    menu.classList.remove("radial-hidden");
    _radialOpen = true;

    // Animate items in
    requestAnimationFrame(() => {
      rings.querySelectorAll(".radial-item").forEach(el => el.classList.add("spawned"));
    });
  }

  function _addGuideCircle(container, cx, cy, r) {
    const el = document.createElement("div");
    el.className = "radial-ring-guide";
    const d = r * 2;
    el.style.cssText = `left:${cx}px;top:${cy}px;width:${d}px;height:${d}px;`;
    container.appendChild(el);
  }

  function _addRadialItem(container, app, idx, total, radius, cx, cy, isOuter) {
    const angle = total === 1
      ? -Math.PI / 2
      : (2 * Math.PI * idx / total) - Math.PI / 2;
    const x = cx + radius * Math.cos(angle);
    const y = cy + radius * Math.sin(angle);

    const item = document.createElement("div");
    item.className = "radial-item" + (isOuter ? " outer" : "");
    item.style.left = x + "px";
    item.style.top = y + "px";
    item.dataset.appId = app.id;
    item.dataset.appName = app.name;

    if (app.icon_url) {
      const img = document.createElement("img");
      img.src = app.icon_url;
      img.alt = app.name;
      img.onerror = () => { img.replaceWith(_makePlaceholder(app.name)); };
      item.appendChild(img);
    } else {
      item.appendChild(_makePlaceholder(app.name));
    }

    item.addEventListener("mouseenter", () => _showRadialLabel(app.name, x, y));
    item.addEventListener("mouseleave", () => _hideRadialLabel());
    item.addEventListener("click", (e) => {
      e.stopPropagation();
      _launchApp(app.id);
    });

    container.appendChild(item);
  }

  function _makePlaceholder(name) {
    const el = document.createElement("div");
    el.className = "radial-item-placeholder";
    el.textContent = (name || "?")[0].toUpperCase();
    return el;
  }

  function _showRadialLabel(name, itemX, itemY) {
    const label = document.getElementById("radial-label");
    label.textContent = name;
    // Position label below the item
    label.style.left = itemX + "px";
    label.style.top = (itemY + 28) + "px";
    label.classList.add("visible");
  }

  function _hideRadialLabel() {
    document.getElementById("radial-label").classList.remove("visible");
  }

  function _launchApp(appId) {
    if (_radialGraphPos) {
      _pendingLaunchPos = { x: _radialGraphPos.x, y: _radialGraphPos.y };
    }
    const msg = { action: "launch_app", app_id: appId };
    if (_radialContext) {
      msg.project_id = _radialContext.project_id;
      if (_radialContext.parent_xid != null) msg.parent_xid = _radialContext.parent_xid;
    }
    sendToBackend(msg);
    hideRadialMenu();
  }

  function hideRadialMenu() {
    if (!_radialOpen) return;
    _radialOpen = false;
    _radialGraphPos = null;
    _radialContext = null;
    const menu = document.getElementById("radial-menu");
    menu.classList.add("radial-hidden");
    document.getElementById("radial-rings").innerHTML = "";
    _hideRadialLabel();
  }

  // ── Boot ──────────────────────────────────────────────────────────────────
  window.ctxAction = ctxAction;
  window.ctxLinkAction = ctxLinkAction;
  document.addEventListener("DOMContentLoaded", init);

})();
