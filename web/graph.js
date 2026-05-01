/* wind_mgr — D3.js force-directed graph */
(function () {
  "use strict";

  const GRAPH_VERSION = "20260501-0020";

  // ── State ────────────────────────────────────────────────────────────────
  let _data = { nodes: [], edges: [], projects: [], active_xid: null };
  // ── Perf counters ─────────────────────────────────────────────────────────
  let _perfUpdateCount = 0, _perfSimCount = 0, _perfThumbCount = 0, _perfLastReport = Date.now();
  function _perfReport() {
    const now = Date.now();
    if (now - _perfLastReport < 10000) return;  // report every 10s
    const elapsed = ((now - _perfLastReport) / 1000).toFixed(1);
    const dom = domStats();
    console.log(
      `[perf] last ${elapsed}s: updateGraph×${_perfUpdateCount}  simRebuild×${_perfSimCount}  thumbUpdates×${_perfThumbCount}  dom=${dom.total} nodes=${dom.cards} hulls=${dom.hulls} links=${dom.links} clips=${dom.clips} imgs=${dom.images} hidden=${dom.hidden}`
    );
    _perfUpdateCount = 0; _perfSimCount = 0; _perfThumbCount = 0;
    _perfLastReport = now;
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
  let _svg = null, _g = null, _zoom = null;
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
  let _dragTargetProject = null;
  let _settleAfterMoveUntil = 0;
  let _lastMiddleClickAt = 0;
  let _panRestoreTimer = null;
  let _panPerformanceActive = false;
  let _deferredThumbItems = [];
  let _pendingZoomTransform = null;
  let _zoomFrame = null;
  let _currentZoomTransform = d3.zoomIdentity;
  let _backendInteractionActive = false;
  let _backendInteractionStopTimer = null;

  const NODE_W  = 180;
  const THUMB_H = 140;
  const INFO_H  = 0;
  const NODE_H  = THUMB_H + INFO_H;
  const LAYOUT = {
    hullPad: 100,
    hullShape: "cards",
    dropIntoPad: 30,
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
    nodeCollideRadius: 120,
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
  };

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    Promise.resolve(window.windMgrConfigReady)
      .then(() => {
        Object.assign(LAYOUT, (window.windMgrConfig || {}).layout || {});
        applySpacingPreset((window.windMgrConfig || {}).layout || {});
        _initInner();
      })
      .catch(e => { console.error("init failed:", e.toString(), e.stack || ""); });
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
      .attr("viewBox", "0 -4 10 8")
      .attr("refX", 18).attr("refY", 0)
      .attr("markerWidth", 6).attr("markerHeight", 6)
      .attr("orient", "auto")
      .append("path")
        .attr("d", "M0,-4L10,0L0,4")
        .attr("fill", "#777");

    _zoom = d3.zoom()
      .scaleExtent([0.08, LAYOUT.maxZoom])
      .filter(zoomFilter)
      .on("start", () => setPanPerformanceMode(true))
      .on("zoom", (e) => scheduleZoomTransform(e.transform));
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
    _g = _svg.append("g").attr("class", "graph-world");

    _g.append("g").attr("class", "hulls-layer");
    _g.append("g").attr("class", "links-layer");
    _g.append("g").attr("class", "nodes-layer");
    _g.append("g").attr("class", "labels-layer");

    document.addEventListener("click", () => { hideContextMenu(); hideLinkContextMenu(); });
    window.addEventListener("keydown", onKeyDown);
    window.addEventListener("keyup", onKeyUp);
    window.addEventListener("blur", () => { _ctrlDown = false; setForceFrozen(false); });
    document.addEventListener("keyup", onKeyUp);
    window.addEventListener("resize", () =>
      _svg.attr("width", window.innerWidth).attr("height", window.innerHeight));

    document.getElementById("btn-refresh").addEventListener("click", () =>
      sendToBackend({ action: "refresh_all_thumbs" }));
    document.getElementById("btn-auto").addEventListener("click", function () {
      this.classList.toggle("active");
      sendToBackend({ action: "toggle_auto_refresh", enabled: this.classList.contains("active") });
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

  function zoomFilter(e) {
    if (e.type === "wheel") return true;
    if (e.type === "mousedown") return e.button === 1;
    if (e.type === "dblclick") return false;
    return !e.button;
  }

  function setPanPerformanceMode(active) {
    if (!_svg) return;
    if (_panRestoreTimer) {
      clearTimeout(_panRestoreTimer);
      _panRestoreTimer = null;
    }
    if (active) {
      _panPerformanceActive = true;
      setBackendInteractionActive(true);
      return;
    }
    _panRestoreTimer = setTimeout(() => {
      _panPerformanceActive = false;
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

  function scheduleZoomTransform(transform) {
    _pendingZoomTransform = transform;
    if (_zoomFrame) return;
    _zoomFrame = requestAnimationFrame(() => {
      _zoomFrame = null;
      if (_pendingZoomTransform) {
        _currentZoomTransform = _pendingZoomTransform;
        applyZoomTransform(_pendingZoomTransform);
        _pendingZoomTransform = null;
      }
    });
  }

  function applyZoomTransform(transform) {
    if (!_g || !transform) return;
    // Use CSS transform for viewport movement. It renders the same graphics as
    // an SVG transform attribute, but avoids forcing WebKit to rebuild SVG
    // image/filter layout on every pan frame.
    _g
      .style("transform", `matrix(${transform.k}, 0, 0, ${transform.k}, ${transform.x}, ${transform.y})`)
      .style("transform-origin", "0 0")
      .style("transform-box", "view-box");
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
    if (_data.active_xid === xid) return;
    _data.active_xid = xid;
    d3.selectAll(".node-g")
      .classed("active-window", d => d.xid === xid)
      .classed("active-window-new", false);
    const active = d3.select(`[data-xid="${xid}"]`);
    if (!active.empty()) {
      active.classed("active-window-new", false);
      requestAnimationFrame(() => active.classed("active-window-new", true));
    }
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

    // First pass: restore positions for existing nodes
    nodes.forEach(n => {
      const sel = _g.select(`[data-xid="${n.xid}"]`);
      const old = sel.node() ? sel.datum() : null;
      if (old) { n.x = old.x; n.y = old.y; n.vx = 0; n.vy = 0; }
    });

    // Second pass: seed new nodes near parent or project anchor (avoids top-left spawn)
    nodes.forEach(n => {
      if (n.x != null) return;
      const par = xidToNode[n.parent_xid];
      const anchor = _projectAnchors[n.project_id];
      if (par && par.x != null) {
        // Place at link distance so sim starts near equilibrium and doesn't need to push hard
        const angle = Math.random() * 2 * Math.PI;
        const d = LAYOUT.sameProjectLinkDistance || 180;
        n.x = par.x + Math.cos(angle) * d;
        n.y = par.y + Math.sin(angle) * d;
      } else {
        const base = anchor || { x: window.innerWidth / 2, y: window.innerHeight / 2 };
        n.x = base.x + (Math.random() - 0.5) * 100;
        n.y = base.y + (Math.random() - 0.5) * 100;
      }
      n.vx = 0; n.vy = 0;
    });

    // ── Nodes DOM ───────────────────────────────────────────────────────
    const node = _g.select(".nodes-layer")
      .selectAll(".node-g")
      .data(nodes, d => d.xid);

    const nodeEnter = node.enter()
      .append("g")
        .attr("class", "node-g")
        .attr("data-xid", d => d.xid)
        .on("click", (e, d) => { e.stopPropagation(); onNodeClick(d); })
        .on("contextmenu", (e, d) => { e.preventDefault(); showContextMenu(e, d); })
        .call(d3.drag()
          .filter(e => !e.button)
          .on("start", dragStarted)
          .on("drag",  dragged)
          .on("end",   dragEnded));

    nodeEnter.append("rect").attr("class", "node-bg");
    nodeEnter.append("clipPath")
      .attr("id", d => `card-clip-${d.xid}`)
      .append("rect").attr("class", "card-clip-rect").attr("rx", 8);
    nodeEnter.append("image")
      .attr("class", "node-thumb")
      .attr("preserveAspectRatio", "xMidYMid meet")
      .attr("clip-path", d => `url(#card-clip-${d.xid})`);
    nodeEnter.append("rect").attr("class", "active-overlay");
    nodeEnter.append("image").attr("class", "node-icon").attr("width", 20).attr("height", 20);
    nodeEnter.append("title");
    nodeEnter.append("path").attr("class", "node-title-bg");
    nodeEnter.append("text").attr("class", "node-title").attr("text-anchor", "middle").attr("x", 0);
    nodeEnter.append("text").attr("class", "node-breadcrumb").attr("text-anchor", "middle").attr("x", 0);

    nodeEnter.merge(node).each(function (d) { renderCard(d3.select(this), d); });
    node.exit().remove();

    // Non-topology updates (thumbnails, titles): skip simulation rebuild to avoid memory/CPU leak
    if (!topologyChanged) {
      renderNodesOnly();
      renderHulls();
      return;
    }
    _perfSimCount++;

    const linkData = edges
      .map(e => ({ source: xidToNode[e.source], target: xidToNode[e.target] }))
      .filter(e => e.source && e.target);

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
      .force("collide", d3.forceCollide(d => Math.max(LAYOUT.nodeCollideRadius, cardRadius(d))))
      .force("cluster", forceCluster(nodes))
      .force("projectCollide", forceProjectCollide(nodes))
      .force("projectRectCollide", forceProjectRectCollide(nodes))
      .force("projectBounds", forceProjectBounds(nodes))
      .force("projectX", d3.forceX(d => (_projectAnchors[d.project_id] || {}).x || window.innerWidth / 2).strength(LAYOUT.projectAnchorStrength))
      .force("projectY", d3.forceY(d => (_projectAnchors[d.project_id] || {}).y || window.innerHeight / 2).strength(LAYOUT.projectAnchorStrength))
      .force("hierarchy", forceHierarchy(linkData))
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
     .classed("selected",      d.xid === _selectedXid);

    const size = cardSize(d);
    const hw = size.w / 2, hh = size.h / 2;
    const displayTitle = d.tab_title || d.project_name || d.title;
    const truncTitle = displayTitle.length > 24 ? displayTitle.slice(0, 23) + "…" : displayTitle;

    g.select(".node-bg")
      .attr("x", -hw).attr("y", -hh)
      .attr("width", size.w).attr("height", size.h)
      .attr("rx", 8);

    g.select(".card-clip-rect")
      .attr("x", -hw).attr("y", -hh)
      .attr("width", size.w).attr("height", size.h);

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

    // Breadcrumb
    const bc = d.breadcrumb || "";
    g.select(".node-breadcrumb")
      .attr("y", hh - 3)
      .text(bc)
      .style("display", "none");

    const hoverLines = [
      displayTitle || d.title || `Window ${d.xid}`,
      d.active_file ? `File: ${d.active_file}` : "",
      d.active_directory ? `Directory: ${d.active_directory}` : "",
    ].filter(Boolean);
    g.select("title").text(hoverLines.join("\n"));
  }

  function updateThumbnails(items) {
    if (_panPerformanceActive) {
      _deferredThumbItems = mergeThumbnailItems(_deferredThumbItems, items || []);
      return;
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
      if (!g.empty()) renderCard(g, node);
    });
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

  function _linkPath(d) {
    const sx = d.source.x || 0, sy = d.source.y || 0;
    const tx = d.target.x || 0, ty = d.target.y || 0;
    const ss = cardSize(d.source), ts = cardSize(d.target);
    const [x1, y1] = rectEdgePoint(sx, sy, tx, ty, ss.w / 2, ss.h / 2);
    const [x2, y2] = rectEdgePoint(tx, ty, sx, sy, ts.w / 2, ts.h / 2);
    // Three-point path so marker-mid places the arrowhead at the midpoint
    const mx = (x1 + x2) / 2, my = (y1 + y2) / 2;
    return `M${x1},${y1}L${mx},${my}L${x2},${y2}`;
  }

  function ticked() {
    _g.select(".links-layer").selectAll(".link").attr("d", _linkPath);
    _g.select(".links-layer").selectAll(".link-hit").attr("d", _linkPath);

    renderNodesOnly();
    renderHulls();
  }

  function renderNodesOnly() {
    _g.select(".nodes-layer").selectAll(".node-g")
      .attr("transform", d => `translate(${d.x || 0},${d.y || 0})`);
  }

  // ── Cluster hulls ─────────────────────────────────────────────────────────
  function renderHulls() {
    const projectGroups = {};
    _data.nodes.filter(n => n.is_alive && n.fx == null).forEach(n => {
      (projectGroups[n.project_id] = projectGroups[n.project_id] || []).push(n);
    });

    const hullData = Object.entries(projectGroups).map(([pid, nodes]) => {
      const proj = _projectMap[pid] || { id: pid, name: pid, color: "#888" };
      const pts = nodes.flatMap(n => {
        const x = n.x || 0, y = n.y || 0, pad = LAYOUT.hullPad;
        const size = cardSize(n), hw = size.w / 2, hh = size.h / 2;
        return [[x-hw-pad,y-hh-pad],[x+hw+pad,y-hh-pad],[x+hw+pad,y+hh+pad],[x-hw-pad,y+hh+pad]];
      });
      const hull = d3.polygonHull(pts);
      const path = LAYOUT.hullShape === "cards"
        ? nodes.map(n => cardRectPath(n, LAYOUT.hullPad)).join("")
        : (hull ? "M" + hull.join("L") + "Z" : "");
      return { pid, proj, hull, path, nodes,
               cx: d3.mean(nodes, n => n.x),
               cy: d3.mean(nodes, n => n.y),
               labelY: d3.min(pts, p => p[1]) - LAYOUT.groupLabelGap };
    }).filter(d => d.path);

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
    const labelsEnter = labels.enter().append("text").attr("class", "cluster-label");
    labelsEnter.merge(labels)
      .attr("x", d => d.cx).attr("y", d => d.labelY)
      .attr("text-anchor", "middle")
      .attr("fill", d => d.proj.color)
      .text(d => d.proj.name);

    hulls.exit().remove();
    labels.exit().remove();
  }

  function cardRectPath(d, pad) {
    const x = d.x || 0, y = d.y || 0;
    const size = cardSize(d), hw = size.w / 2 + pad, hh = size.h / 2 + pad;
    return `M${x - hw},${y - hh}H${x + hw}V${y + hh}H${x - hw}Z`;
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
    const width = Math.max(cellW * cols, window.innerWidth - marginX * 2);
    const height = Math.max(cellH * rows, window.innerHeight - marginY * 2);

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

  function forceHierarchy(linkData) {
    const gap = LAYOUT.hierarchyGap != null ? LAYOUT.hierarchyGap : 120;
    const strength = LAYOUT.hierarchyStrength != null ? LAYOUT.hierarchyStrength : 0.2;
    return function force(alpha) {
      linkData.forEach(e => {
        const p = e.source, c = e.target;
        if (!p || !c) return;
        const dy = (c.y || 0) - (p.y || 0);
        if (dy < gap) {
          const push = (gap - dy) * strength * alpha * 0.5;
          c.vy += push;
          p.vy -= push;
        }
      });
    };
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
    _dragStart = { x: e.x, y: e.y, project_id: d.project_id };
    _dragOriginProject = d.project_id;
    _dragMoved = false;
    _dragTargetProject = null;
    _dragDropHulls = buildDropHulls(d);
    setDragFeedback(d, null);
    d.fx = d.x; d.fy = d.y;
  }
  function dragged(e, d) {
    if (eventWantsFreeze(e)) _dragFreeze = true;
    if (_simulation) _simulation.stop();
    const moved = _dragStart ? Math.hypot(e.x - _dragStart.x, e.y - _dragStart.y) : 0;
    if (!_dragMoved && moved >= 8) _dragMoved = true;
    d.fx = e.x; d.fy = e.y;
    d.x = e.x; d.y = e.y;
    _dragTargetProject = findDropProject(e.x, e.y, d);
    setDragFeedback(d, _dragTargetProject);
    renderNodesOnly();
    renderHulls();
  }
  function dragEnded(e, d) {
    const moved = _dragStart ? Math.hypot(e.x - _dragStart.x, e.y - _dragStart.y) : 0;
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
    if (moved < 8) {
      if (restore) { d.x = restore.x; d.y = restore.y; }
      stopLayoutMotion();
      renderNodesOnly();
      clearDragFeedback();
      _dragDropHulls = null;
      _dragActive = false;
      _dragOriginProject = null;
      _dragTargetProject = null;
      flushQueuedGraphData();
      return;
    }

    const target = _dragTargetProject;
    const ownProject = soloProjectId(d.xid);
    let nextProject = target || ownProject;
    if (target === startedProject && isRecentDetach(d.xid, startedProject)) {
      nextProject = ownProject;
      console.log("ignored stale reinsertion", d.xid, startedProject);
    }
    console.log("drag release", d.xid, "from", startedProject, "target", target, "next", nextProject);
    const projectChanged = nextProject !== d.project_id;
    if (projectChanged) {
      if (nextProject === ownProject && startedProject !== ownProject) {
        _recentDetach[d.xid] = { from: startedProject, until: Date.now() + 6000 };
      }
      d.project_id = nextProject;
      _projectAnchors = computeProjectAnchors(_data.nodes.filter(n => n.is_alive));
      sendToBackend({ action: "move_node", xid: d.xid, project_id: nextProject, with_children: true });
    }

    clearDragFeedback();
    _dragDropHulls = null;
    _dragActive = false;
    _dragOriginProject = null;
    _dragTargetProject = null;
    renderHulls();
    if (projectChanged) {
      _queuedGraphData = null;
      _settleAfterMoveUntil = Date.now() + 2500;
    }
    else flushQueuedGraphData();
    if (!keepFrozen && !_forceFrozen) restartLayout(projectChanged ? 0.65 : 0.08);
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

  function soloProjectId(xid) {
    return `${xid}:solo`;
  }

  function findDropProject(x, y, dragged) {
    const containing = findProjectContainingPoint(x, y, dragged);
    if (containing) return containing;
    if (!LAYOUT.dropNearestDistance || LAYOUT.dropNearestDistance <= 0) return null;
    return findNearestProject(x, y, dragged.project_id, LAYOUT.dropNearestDistance);
  }

  function findProjectContainingPoint(x, y, dragged) {
    const hulls = _dragDropHulls || buildDropHulls(dragged);
    for (const h of hulls) {
      if (h.rects && h.rects.some(r => x >= r.x0 && x <= r.x1 && y >= r.y0 && y <= r.y1)) {
        return h.pid;
      }
      if (h.pid !== _dragOriginProject && h.hull && d3.polygonContains(h.hull, [x, y])) return h.pid;
    }
    return null;
  }

  function buildDropHulls(dragged) {
    return _data.projects.map(p => {
      const members = _data.nodes.filter(n =>
        n.is_alive && n.project_id === p.id && n.xid !== dragged.xid);
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

  function setDragFeedback(d, targetProjectId) {
    const node = d3.select(`[data-xid="${d.xid}"]`);
    node.classed("dragging", true)
      .classed("drop-into", !!targetProjectId)
      .classed("drop-out", !targetProjectId);

    _g.select(".hulls-layer").selectAll(".hull-group")
      .classed("drop-target", h => !!targetProjectId && h.pid === targetProjectId);
  }

  function clearDragFeedback() {
    d3.selectAll(".node-g")
      .classed("dragging", false)
      .classed("drop-into", false)
      .classed("drop-out", false);
    _g.select(".hulls-layer").selectAll(".hull-group")
      .classed("drop-target", false);
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
  function onNodeClick(d) {
    stopLayoutMotion();
    _selectedXid = d.xid;
    d3.selectAll(".node-g").classed("selected", false);
    d3.select(`[data-xid="${d.xid}"]`).classed("selected", true);
    sendToBackend({ action: "activate", xid: d.xid });
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

    // Pre-place each node near its project anchor so render()'s position-
    // preservation code picks up fresh coordinates instead of stale ones,
    // and forces don't need to drag cards all the way from (0,0).
    _g.select(".nodes-layer").selectAll(".node-g").each(function (d) {
      const a = _projectAnchors[d.project_id] || {};
      d.x = (a.x || window.innerWidth / 2)  + (Math.random() - 0.5) * 80;
      d.y = (a.y || window.innerHeight / 2) + (Math.random() - 0.5) * 80;
      d.vx = 0; d.vy = 0;
    });

    render(true);   // simulation starts from anchor positions
    fitView();      // frame those positions immediately (no jump)
  }

  function fitView() {
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
    _svg.transition().duration(600)
      .call(_zoom.transform, d3.zoomIdentity.translate(tx, ty).scale(scale));
  }

  // ── Context menu ──────────────────────────────────────────────────────────
  let _ctxNode = null;

  function showContextMenu(e, d) {
    _ctxNode = d;
    const menu = document.getElementById("context-menu");
    e.stopPropagation();

    const projectList = document.getElementById("ctx-project-list");
    projectList.innerHTML = "";
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
    });

    menu.style.display = "block";
    menu.style.left = "0px";
    menu.style.top = "0px";
    const rect = menu.getBoundingClientRect();
    const pad = 8;
    menu.style.left = Math.max(pad, Math.min(e.clientX, window.innerWidth - rect.width - pad)) + "px";
    menu.style.top = Math.max(pad, Math.min(e.clientY, window.innerHeight - rect.height - pad)) + "px";
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
    } else if (action === "refresh_thumb") {
      sendToBackend({ action: "refresh_thumb", xid: _ctxNode.xid });
    } else if (action === "rename_project") {
      const name = prompt("New project name:", _projectMap[_ctxNode.project_id]?.name || "");
      if (name) sendToBackend({ action: "rename_project", project_id: _ctxNode.project_id, name });
    } else if (action === "detach") {
      sendToBackend({ action: "remove_link", xid: _ctxNode.xid });
    }
    hideContextMenu();
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

  // ── Boot ──────────────────────────────────────────────────────────────────
  window.ctxAction = ctxAction;
  window.ctxLinkAction = ctxLinkAction;
  document.addEventListener("DOMContentLoaded", init);

})();
