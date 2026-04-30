/* wind_mgr — D3.js force-directed graph */
(function () {
  "use strict";

  // ── State ────────────────────────────────────────────────────────────────
  let _data = { nodes: [], edges: [], projects: [], active_xid: null };
  let _simulation = null;
  let _svg = null, _g = null, _zoom = null;
  let _nodeMap = {};
  let _projectMap = {};
  let _selectedXid = null;
  let _initialized = false;
  let _pendingData = null;
  let _projectAnchors = {};
  let _dragFreeze = false;
  let _forceFrozen = false;
  let _ctrlDown = false;
  let _dragStart = null;
  let _dragDropHulls = null;
  let _lastMiddleClickAt = 0;

  const NODE_W  = 180;
  const THUMB_H = 140;
  const INFO_H  = 0;
  const NODE_H  = THUMB_H + INFO_H;
  const HW      = NODE_W / 2;   // half-width  = 90
  const HH      = NODE_H / 2;   // half-height = 70
  const LAYOUT = {
    hullPad: 100,
    projectMarginX: 220,
    projectMarginY: 170,
    projectCellW: 520,
    projectCellH: 400,
    sameProjectLinkDistance: 180,
    crossProjectLinkDistance: 680,
    sameProjectLinkStrength: 0.35,
    crossProjectLinkStrength: 0.01,
    nodeCharge: -400,
    nodeCollideRadius: 120,
    clusterStrength: 0.18,
    projectCirclePadding: 260,
    projectCircleStrength: 0.25,
    projectRectGap: 180,
    projectRectStrength: 0.7,
    foreignCardBoundaryGap: 60,
    foreignCardBoundaryStrength: 0.55,
    projectAnchorStrength: 0.08,
    centerStrength: 0.03,
    velocityDecay: 0.65,
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
        _initInner();
      })
      .catch(e => { console.error("init failed:", e.toString(), e.stack || ""); });
  }

  function _initInner() {
    _svg = d3.select("svg#graph");

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

    // Clip path for thumbnail rounding (applied in node's local coordinate space)
    defs.append("clipPath").attr("id", "thumb-clip")
      .append("rect")
        .attr("x", -HW).attr("y", -HH)
        .attr("width", NODE_W).attr("height", THUMB_H)
        .attr("rx", 6);

    _zoom = d3.zoom()
      .scaleExtent([0.08, LAYOUT.maxZoom])
      .filter(zoomFilter)
      .on("zoom", (e) => _g.attr("transform", e.transform));

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
    _g = _svg.append("g");

    _g.append("g").attr("class", "hulls-layer");
    _g.append("g").attr("class", "links-layer");
    _g.append("g").attr("class", "nodes-layer");
    _g.append("g").attr("class", "labels-layer");

    document.addEventListener("click", hideContextMenu);
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
    window.setInterval(() => sendToBackend({ action: "refresh_active" }), 300);

    _initialized = true;
    console.log("wind_mgr init complete");
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

  // ── Public API ────────────────────────────────────────────────────────────
  window.windMgr = {
    updateGraph(data) {
      if (!_initialized) {
        console.warn("windMgr.updateGraph called before init — queuing");
        _pendingData = data;
        return;
      }
      _data = data;
      _nodeMap = {};
      _projectMap = {};
      data.nodes.forEach(n => { _nodeMap[n.xid] = n; });
      data.projects.forEach(p => { _projectMap[p.id] = p; });
      render();
      updateStatus();
    },

    setActiveWindow(xid) {
      setActiveWindow(xid);
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

  // ── Render ────────────────────────────────────────────────────────────────
  function render() {
    const nodes = _data.nodes.filter(n => n.is_alive);
    const edges = _data.edges.filter(e => {
      const s = _nodeMap[e.source], t = _nodeMap[e.target];
      return s && t && s.is_alive && t.is_alive;
    });

    _projectAnchors = computeProjectAnchors(nodes);

    // Preserve existing positions, but drop old velocity so refreshes do not
    // keep reintroducing drift into a newly started simulation.
    nodes.forEach(n => {
      const sel = _g.select(`[data-xid="${n.xid}"]`);
      const old = sel.node() ? sel.datum() : null;
      if (old) { n.x = old.x; n.y = old.y; n.vx = 0; n.vy = 0; }
    });

    const xidToNode = {};
    nodes.forEach(n => { xidToNode[n.xid] = n; });
    const linkData = edges
      .map(e => ({ source: xidToNode[e.source], target: xidToNode[e.target] }))
      .filter(e => e.source && e.target);

    // ── Simulation ──────────────────────────────────────────────────────
    if (_simulation) _simulation.stop();
    _simulation = d3.forceSimulation(nodes)
      .force("link", d3.forceLink(linkData).id(d => d.xid)
        .distance(d => d.source.project_id === d.target.project_id
          ? LAYOUT.sameProjectLinkDistance : LAYOUT.crossProjectLinkDistance)
        .strength(d => d.source.project_id === d.target.project_id
          ? LAYOUT.sameProjectLinkStrength : LAYOUT.crossProjectLinkStrength))
      .force("charge", d3.forceManyBody().strength(LAYOUT.nodeCharge))
      .force("collide", d3.forceCollide(LAYOUT.nodeCollideRadius))
      .force("cluster", forceCluster(nodes))
      .force("projectCollide", forceProjectCollide(nodes))
      .force("projectRectCollide", forceProjectRectCollide(nodes))
      .force("projectBounds", forceProjectBounds(nodes))
      .force("projectX", d3.forceX(d => (_projectAnchors[d.project_id] || {}).x || window.innerWidth / 2).strength(LAYOUT.projectAnchorStrength))
      .force("projectY", d3.forceY(d => (_projectAnchors[d.project_id] || {}).y || window.innerHeight / 2).strength(LAYOUT.projectAnchorStrength))
      .force("center", d3.forceCenter(window.innerWidth / 2, window.innerHeight / 2).strength(LAYOUT.centerStrength))
      .velocityDecay(LAYOUT.velocityDecay)
      .alphaDecay(LAYOUT.alphaDecay)
      .on("tick", ticked);
    if (_forceFrozen) _simulation.stop();

    // ── Edges ──────────────────────────────────────────────────────────
    const link = _g.select(".links-layer")
      .selectAll(".link").data(linkData, d => `${d.source.xid}-${d.target.xid}`);
    link.enter().append("path").attr("class", "link").merge(link);
    link.exit().remove();

    // ── Nodes (SVG native: g > rect + image + text) ─────────────────
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

    // Card background
    nodeEnter.append("rect")
      .attr("class", "node-bg")
      .attr("x", -HW).attr("y", -HH)
      .attr("width", NODE_W).attr("height", NODE_H)
      .attr("rx", 8);

    // Thumbnail image (clipped to top portion)
    nodeEnter.append("image")
      .attr("class", "node-thumb")
      .attr("x", -HW).attr("y", -HH)
      .attr("width", NODE_W).attr("height", THUMB_H)
      .attr("preserveAspectRatio", "xMidYMid slice")
      .attr("clip-path", "url(#thumb-clip)");

    // App icon badge
    nodeEnter.append("image")
      .attr("class", "node-icon")
      .attr("width", 20).attr("height", 20);

    nodeEnter.append("title");

    // Title
    nodeEnter.append("text")
      .attr("class", "node-title")
      .attr("text-anchor", "middle")
      .attr("x", 0);

    // Breadcrumb
    nodeEnter.append("text")
      .attr("class", "node-breadcrumb")
      .attr("text-anchor", "middle")
      .attr("x", 0);

    const allNodes = nodeEnter.merge(node);
    allNodes.each(function (d) { renderCard(d3.select(this), d); });
    node.exit().remove();
  }

  function renderCard(g, d) {
    g.classed("active-window", d.xid === _data.active_xid)
     .classed("selected",      d.xid === _selectedXid);

    const displayTitle = d.tab_title || d.project_name || d.title;
    const truncTitle = displayTitle.length > 24 ? displayTitle.slice(0, 23) + "…" : displayTitle;

    // Thumbnail
    g.select(".node-thumb")
      .attr("href", d.thumb_url || "")
      .style("display", d.thumb_url ? null : "none");

    // Thumb placeholder emoji when no image
    // (handled by SVG text fallback if thumb fails — use onerror equivalent via error event)

    // App icon
    const hasIcon = !!d.icon_url;
    g.select(".node-icon")
      .attr("href", d.icon_url || "")
      .attr("x", HW - 24)
      .attr("y", -HH + THUMB_H - 24)
      .style("display", hasIcon ? null : "none");

    // Title — center in info bar
    g.select(".node-title")
      .attr("y", HH - 8)
      .text(truncTitle);

    // Breadcrumb
    const bc = d.breadcrumb || "";
    g.select(".node-breadcrumb")
      .attr("y", HH - 3)
      .text(bc)
      .style("display", "none");

    g.select("title").text(displayTitle || d.title || `Window ${d.xid}`);
  }

  function ticked() {
    _g.select(".links-layer").selectAll(".link")
      .attr("d", d => {
        const sx = d.source.x, sy = d.source.y;
        const tx = d.target.x, ty = d.target.y;
        const dx = tx - sx, dy = ty - sy;
        const dr = Math.sqrt(dx * dx + dy * dy) * 1.4;
        return `M${sx},${sy} A${dr},${dr} 0 0,1 ${tx},${ty}`;
      });

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
    _data.nodes.filter(n => n.is_alive).forEach(n => {
      (projectGroups[n.project_id] = projectGroups[n.project_id] || []).push(n);
    });

    const hullData = Object.entries(projectGroups).map(([pid, nodes]) => {
      const proj = _projectMap[pid] || { id: pid, name: pid, color: "#888" };
      const pts = nodes.flatMap(n => {
        const x = n.x || 0, y = n.y || 0, pad = LAYOUT.hullPad;
        return [[x-pad,y-pad],[x+pad,y-pad],[x+pad,y+pad],[x-pad,y+pad]];
      });
      const hull = d3.polygonHull(pts);
      return { pid, proj, hull, nodes,
               cx: d3.mean(nodes, n => n.x),
               cy: d3.mean(nodes, n => n.y),
               labelY: d3.min(pts, p => p[1]) - LAYOUT.groupLabelGap };
    }).filter(d => d.hull);

    const hulls = _g.select(".hulls-layer")
      .selectAll(".hull-group").data(hullData, d => d.pid);

    const enter = hulls.enter().append("g").attr("class", "hull-group");
    enter.append("path").attr("class", "cluster-hull")
      .on("dblclick", (e, d) => toggleProject(d.pid));

    const all = enter.merge(hulls);
    all.select(".cluster-hull")
      .attr("d", d => "M" + d.hull.join("L") + "Z")
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

  // ── Force cluster ─────────────────────────────────────────────────────────
  function computeProjectAnchors(nodes) {
    const ids = Array.from(new Set(nodes.map(n => n.project_id))).sort();
    const anchors = {};
    if (!ids.length) return anchors;

    const cols = Math.ceil(Math.sqrt(ids.length));
    const rows = Math.ceil(ids.length / cols);
    const marginX = LAYOUT.projectMarginX, marginY = LAYOUT.projectMarginY;
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

  function forceCluster(nodes) {
    const strength = LAYOUT.clusterStrength;
    return function force(alpha) {
      const centroids = {}, counts = {};
      nodes.forEach(n => {
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
      const r = d3.max(members, n => Math.hypot((n.x || 0) - x, (n.y || 0) - y)) || 0;
      return { pid, nodes: members, x, y, r: r + 120 };
    });
  }

  function projectRects(nodes, margin) {
    const byProject = {};
    nodes.forEach(n => {
      if (!n.is_alive) return;
      (byProject[n.project_id] = byProject[n.project_id] || []).push(n);
    });
    return Object.entries(byProject).map(([pid, members]) => {
      const x0 = d3.min(members, n => (n.x || 0) - margin);
      const x1 = d3.max(members, n => (n.x || 0) + margin);
      const y0 = d3.min(members, n => (n.y || 0) - margin);
      const y1 = d3.max(members, n => (n.y || 0) + margin);
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
      _simulation.alpha(0.08).restart();
    }
  }

  function eventWantsFreeze(e) {
    return _ctrlDown || _forceFrozen || !!(e.sourceEvent && e.sourceEvent.ctrlKey);
  }

  function dragStarted(e, d) {
    _dragFreeze = eventWantsFreeze(e);
    if (_dragFreeze) _simulation.stop();
    else if (!e.active) _simulation.alphaTarget(0.3).restart();
    _dragStart = { x: e.x, y: e.y };
    _dragDropHulls = buildDropHulls(d);
    setDragFeedback(d, null);
    d.fx = d.x; d.fy = d.y;
  }
  function dragged(e, d) {
    _dragFreeze = eventWantsFreeze(e);
    if (_dragFreeze) {
      _forceFrozen = true;
      _simulation.stop();
    }
    d.fx = e.x; d.fy = e.y;
    d.x = e.x; d.y = e.y;
    setDragFeedback(d, findDropProject(e.x, e.y, d));
    if (_dragFreeze) renderNodesOnly();
  }
  function dragEnded(e, d) {
    const wasFrozen = _dragFreeze;
    const moved = _dragStart ? Math.hypot(e.x - _dragStart.x, e.y - _dragStart.y) : 0;
    _dragStart = null;
    _dragFreeze = false;
    if (!_ctrlDown && !(e.sourceEvent && e.sourceEvent.ctrlKey)) setForceFrozen(false);
    if (!e.active) _simulation.alphaTarget(0);
    d.fx = null; d.fy = null;
    d.vx = 0; d.vy = 0;
    if (moved < 8) {
      clearDragFeedback();
      _dragDropHulls = null;
      return;
    }

    const target = findDropProject(e.x, e.y, d);
    const ownProject = String(d.xid);
    const nextProject = target || ownProject;
    if (nextProject !== d.project_id) {
      d.project_id = nextProject;
      _projectAnchors = computeProjectAnchors(_data.nodes.filter(n => n.is_alive));
      sendToBackend({ action: "move_node", xid: d.xid, project_id: nextProject, with_children: false });
    }

    clearDragFeedback();
    _dragDropHulls = null;
    if (wasFrozen && !_forceFrozen) _simulation.alpha(0.15).restart();
  }

  function findDropProject(x, y, dragged) {
    const containing = findProjectContainingPoint(x, y, dragged);
    if (containing) return containing;
    return findNearestProject(x, y, dragged.project_id, 140);
  }

  function findProjectContainingPoint(x, y, dragged) {
    const hulls = _dragDropHulls || buildDropHulls(dragged);
    for (const h of hulls) {
      if (h.hull && d3.polygonContains(h.hull, [x, y])) return h.pid;
    }
    return null;
  }

  function buildDropHulls(dragged) {
    return _data.projects.map(p => {
      const members = _data.nodes.filter(n =>
        n.is_alive && n.project_id === p.id && n.xid !== dragged.xid);
      if (!members.length) return { pid: p.id, hull: null };
      const pad = LAYOUT.hullPad + LAYOUT.foreignCardBoundaryGap;
      const pts = members.flatMap(n => [
        [(n.x || 0) - pad, (n.y || 0) - pad],
        [(n.x || 0) + pad, (n.y || 0) - pad],
        [(n.x || 0) + pad, (n.y || 0) + pad],
        [(n.x || 0) - pad, (n.y || 0) + pad],
      ]);
      return { pid: p.id, hull: d3.polygonHull(pts) };
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
    _selectedXid = d.xid;
    d3.selectAll(".node-g").classed("selected", false);
    d3.select(`[data-xid="${d.xid}"]`).classed("selected", true);
    sendToBackend({ action: "activate", xid: d.xid });
  }

  function toggleProject(pid) {
    sendToBackend({ action: "toggle_project", project_id: pid });
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
        sendToBackend({ action: "move_node", xid: d.xid, project_id: p.id, with_children: false });
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

  function ctxAction(action) {
    if (!_ctxNode) return;
    if (action === "activate") {
      sendToBackend({ action: "activate", xid: _ctxNode.xid });
    } else if (action === "refresh_thumb") {
      sendToBackend({ action: "refresh_thumb", xid: _ctxNode.xid });
    } else if (action === "rename_project") {
      const name = prompt("New project name:", _projectMap[_ctxNode.project_id]?.name || "");
      if (name) sendToBackend({ action: "rename_project", project_id: _ctxNode.project_id, name });
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
  document.addEventListener("DOMContentLoaded", init);

})();
