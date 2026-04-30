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

  const NODE_W  = 180;
  const THUMB_H = 112;
  const INFO_H  = 28;
  const NODE_H  = THUMB_H + INFO_H;
  const HW      = NODE_W / 2;   // half-width  = 90
  const HH      = NODE_H / 2;   // half-height = 70

  // ── Init ─────────────────────────────────────────────────────────────────
  function init() {
    try { _initInner(); }
    catch(e) { console.error("init failed:", e.toString(), e.stack || ""); }
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
      .scaleExtent([0.08, 3])
      .on("zoom", (e) => _g.attr("transform", e.transform));

    _svg.call(_zoom);
    _g = _svg.append("g");

    _g.append("g").attr("class", "hulls-layer");
    _g.append("g").attr("class", "links-layer");
    _g.append("g").attr("class", "nodes-layer");

    document.addEventListener("click", hideContextMenu);
    window.addEventListener("resize", () =>
      _svg.attr("width", window.innerWidth).attr("height", window.innerHeight));

    document.getElementById("btn-refresh").addEventListener("click", () =>
      sendToBackend({ action: "refresh_all_thumbs" }));
    document.getElementById("btn-auto").addEventListener("click", function () {
      this.classList.toggle("active");
      sendToBackend({ action: "toggle_auto_refresh", enabled: this.classList.contains("active") });
    });
    document.getElementById("btn-fit").addEventListener("click", fitView);

    _initialized = true;
    console.log("wind_mgr init complete");
    if (_pendingData) {
      window.windMgr.updateGraph(_pendingData);
      _pendingData = null;
    }
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
      .force("link", d3.forceLink(linkData).id(d => d.xid).distance(220).strength(0.4))
      .force("charge", d3.forceManyBody().strength(-400))
      .force("collide", d3.forceCollide(120))
      .force("cluster", forceCluster(nodes))
      .force("projectX", d3.forceX(d => (_projectAnchors[d.project_id] || {}).x || window.innerWidth / 2).strength(0.08))
      .force("projectY", d3.forceY(d => (_projectAnchors[d.project_id] || {}).y || window.innerHeight / 2).strength(0.08))
      .force("center", d3.forceCenter(window.innerWidth / 2, window.innerHeight / 2).strength(0.03))
      .velocityDecay(0.65)
      .alphaDecay(0.03)
      .on("tick", ticked);

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

    // Info bar background
    nodeEnter.append("rect")
      .attr("class", "node-info-bg")
      .attr("x", -HW).attr("y", -HH + THUMB_H)
      .attr("width", NODE_W).attr("height", INFO_H)
      .attr("rx", 0);

    // App icon badge
    nodeEnter.append("image")
      .attr("class", "node-icon")
      .attr("width", 20).attr("height", 20);

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
      .attr("y", -HH + THUMB_H + INFO_H / 2 + 4)
      .text(truncTitle);

    // Breadcrumb
    const bc = d.breadcrumb || "";
    g.select(".node-breadcrumb")
      .attr("y", -HH + THUMB_H + INFO_H - 3)
      .text(bc)
      .style("display", bc ? null : "none");
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

    _g.select(".nodes-layer").selectAll(".node-g")
      .attr("transform", d => `translate(${d.x || 0},${d.y || 0})`);

    renderHulls();
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
        const x = n.x || 0, y = n.y || 0, pad = 80;
        return [[x-pad,y-pad],[x+pad,y-pad],[x+pad,y+pad],[x-pad,y+pad]];
      });
      const hull = d3.polygonHull(pts);
      return { pid, proj, hull, nodes,
               cx: d3.mean(nodes, n => n.x), cy: d3.mean(nodes, n => n.y) };
    }).filter(d => d.hull);

    const hulls = _g.select(".hulls-layer")
      .selectAll(".hull-group").data(hullData, d => d.pid);

    const enter = hulls.enter().append("g").attr("class", "hull-group");
    enter.append("path").attr("class", "cluster-hull")
      .on("dblclick", (e, d) => toggleProject(d.pid));
    enter.append("text").attr("class", "cluster-label");

    const all = enter.merge(hulls);
    all.select(".cluster-hull")
      .attr("d", d => "M" + d.hull.join("L") + "Z")
      .attr("fill", d => d.proj.color)
      .attr("stroke", d => d.proj.color);
    all.select(".cluster-label")
      .attr("x", d => d.cx).attr("y", d => d.cy - 85)
      .attr("text-anchor", "middle")
      .attr("fill", d => d.proj.color)
      .text(d => d.proj.name);

    hulls.exit().remove();
  }

  // ── Force cluster ─────────────────────────────────────────────────────────
  function computeProjectAnchors(nodes) {
    const ids = Array.from(new Set(nodes.map(n => n.project_id))).sort();
    const anchors = {};
    if (!ids.length) return anchors;

    const cols = Math.ceil(Math.sqrt(ids.length));
    const rows = Math.ceil(ids.length / cols);
    const marginX = 180, marginY = 150;
    const width = Math.max(1, window.innerWidth - marginX * 2);
    const height = Math.max(1, window.innerHeight - marginY * 2);

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
    const strength = 0.18;
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

  // ── Drag ──────────────────────────────────────────────────────────────────
  function dragStarted(e, d) {
    if (!e.active) _simulation.alphaTarget(0.3).restart();
    d.fx = d.x; d.fy = d.y;
  }
  function dragged(e, d) { d.fx = e.x; d.fy = e.y; }
  function dragEnded(e, d) {
    if (!e.active) _simulation.alphaTarget(0);
    d.fx = null; d.fy = null;
    const snap = findNearestProject(e.x, e.y, d.project_id, 120);
    if (snap) sendToBackend({ action: "move_node", xid: d.xid, project_id: snap, with_children: false });
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
    const x0 = Math.min(...xs) - 140, y0 = Math.min(...ys) - 120;
    const x1 = Math.max(...xs) + 140, y1 = Math.max(...ys) + 120;
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
    menu.style.display = "block";
    menu.style.left = Math.min(e.clientX, window.innerWidth  - 200) + "px";
    menu.style.top  = Math.min(e.clientY, window.innerHeight - 200) + "px";
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
