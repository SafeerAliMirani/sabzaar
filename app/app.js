/* Sabzaar - Larkana tree canopy, heat, and where to plant.
   Real satellite overlays (see convert.py + DATA_SOURCES.md) on a free MapLibre map.
   No token, no backend. */

const DATA = "data/meta.json";

/* ---- native shade-tree guide (editorial, kept separate from the data) ---- */
const TREES = [
  { cn:"Neem", ur:"نیم", bo:"Azadirachta indica",
    desc:"The workhorse shade tree of Sindh. Hardy, drought-tolerant once rooted, casts dense evergreen shade and has well-known medicinal and pest-repellent value.",
    plant:"Streets, schoolyards, courtyards, bus stops. The safest all-rounder for the city.",
    water:"Low", shade:"Dense", size:"15-20 m" },
  { cn:"Peepal", ur:"پیپل", bo:"Ficus religiosa",
    desc:"A large, long-lived fig giving broad, cooling shade and strong support for birds.",
    plant:"Parks, open ground, shrine and graveyard edges. Keep well clear of walls, pipes and drains.",
    water:"Medium", shade:"Very broad", size:"20-25 m" },
  { cn:"Banyan", ur:"بڑ", bo:"Ficus benghalensis",
    desc:"The classic village canopy. Enormous spread and deep shade, and a true habitat tree.",
    plant:"Large parks, maidans and canal-side open ground where it has room to spread.",
    water:"Medium", shade:"Vast", size:"20-25 m" },
  { cn:"Amaltas", ur:"املتاس", bo:"Cassia fistula",
    desc:"Mid-sized, tough and drought-tolerant, with hanging golden flowers in summer.",
    plant:"Avenues, road medians and narrower streets where a big tree will not fit.",
    water:"Low", shade:"Moderate", size:"10-15 m" },
  { cn:"Jamun", ur:"جامن", bo:"Syzygium cumini",
    desc:"Dense, dark evergreen shade plus edible fruit that feeds people and birds.",
    plant:"Roadsides, park edges and along walking routes. A reliable street tree.",
    water:"Medium", shade:"Dense", size:"12-18 m" },
  { cn:"Arjun", ur:"ارجن", bo:"Terminalia arjuna",
    desc:"Large evergreen native to riverbanks, so it suits canal and watercourse edges. Holds soil well.",
    plant:"Canal and watercourse banks, and large open plots with moisture at depth.",
    water:"Medium", shade:"Broad", size:"18-25 m" },
  { cn:"Siris", ur:"سریں", bo:"Albizia lebbeck",
    desc:"Fast-growing feathery-canopy shade tree that fixes nitrogen and improves soil.",
    plant:"Quick shade on degraded or vacant plots, and boundary rows. Prune for form.",
    water:"Low", shade:"Light-moderate", size:"15-20 m" },
  { cn:"Ber", ur:"بیر", bo:"Ziziphus mauritiana",
    desc:"Extremely drought-hardy small tree with edible fruit, for the driest, most neglected edges.",
    plant:"Dry, bare margins and desert edges where little else will survive. Thorny, so site with care.",
    water:"Very low", shade:"Moderate", size:"6-12 m" },
];

const OSM_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const BASE = {
  map: { tiles: ["https://basemaps.cartocdn.com/rastertiles/voyager/{z}/{x}/{y}.png"],
         attribution: OSM_ATTR + ' &copy; <a href="https://carto.com/attributions">CARTO</a>' },
  sat: { tiles: ["https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}"],
         attribution: 'Imagery &copy; Esri, Maxar, Earthstar Geographics' },
};
function baseStyle(kind){
  const b = BASE[kind];
  const [W,S,E,N] = META.bbox;
  const box = [[W,N],[E,N],[E,S],[W,S]];
  const canopyPM = "pmtiles://" + new URL("data/canopy.pmtiles", location.href).href;
  const vis = (id) => (active[id] ? "visible" : "none");
  const rast = (id, extra) => ({ id, type:"raster", source:id,
    layout:{ visibility:vis(id) },
    paint:Object.assign({ "raster-opacity":overlayOpacity, "raster-fade-duration":0 }, extra || {}) });
  return {
    version:8,
    sources:{
      base:{ type:"raster", tiles:b.tiles, tileSize:256, attribution:b.attribution },
      heat:{ type:"image", url:"data/heat.png", coordinates:box },
      landcover:{ type:"image", url:"data/landcover.png", coordinates:box },
      canopy:{ type:"raster", url:canopyPM, tileSize:256 },
      priority:{ type:"image", url:"data/priority.png", coordinates:box },
    },
    // bottom -> top: base, heat, landcover, canopy, priority
    layers:[
      { id:"bg", type:"background", paint:{ "background-color":"#0f1a14" } },
      { id:"base", type:"raster", source:"base" },
      rast("heat"), rast("landcover", { "raster-resampling":"nearest" }),
      rast("canopy"), rast("priority"),
    ],
  };
}

let META = null, PHOTOS = {}, map = null;
const OVERLAY_IDS = ["heat", "landcover", "canopy", "priority"]; // bottom -> top
const active = { canopy:false, landcover:false, heat:false, priority:true };
let overlayOpacity = 0.85;

let labelActive = false;   // OSM place/street/landmark labels

// GPU point clouds (deck.gl)
let deckOverlay = null;
const POINTS = {};                 // id -> Float32Array, lazy-loaded
const pointActive = { trees:false, plant:false };
const POINT_STYLE = {
  trees: { color:[70,190,110,200], label:"Existing trees", sub:"canopy ≥ 3 m, from the height model" },
  plant: { color:[232,90,156,205], label:"Planting spots", sub:"open, hot, treeless land" },
};

init();

async function init(){
  // register the PMTiles protocol so the canopy tileset can be served from one file
  if (typeof pmtiles !== "undefined" && !window.__pmreg) {
    maplibregl.addProtocol("pmtiles", new pmtiles.Protocol().tile);
    window.__pmreg = true;
  }
  META = await (await fetch(DATA)).json();
  try {
    const p = await (await fetch("data/trees/photos.json")).json();
    p.forEach((r) => (PHOTOS[r.slug] = r));
  } catch (e) { /* photos optional */ }

  map = new maplibregl.Map({
    container:"map", style:baseStyle("map"), center:META.center,
    zoom:13, minZoom:9, maxZoom:18, attributionControl:{ compact:false },
  });
  map.addControl(new maplibregl.NavigationControl({ showCompass:false }), "top-right");
  map.addControl(new maplibregl.ScaleControl({ maxWidth:120, unit:"metric" }), "bottom-left");

  buildUI();

  // Overlays are baked into the style, so they load at construction (like the base
  // layer). Just fit the view and paint; the render loop is nudged below.
  map.jumpTo({ center:META.center, zoom:13 });
  map.resize();
  updateMapLegend();
  map.triggerRepaint();
  map.on("styledata", () => { if (labelActive) addLabels(); });
  // pmtiles keeps isStyleLoaded() false, so MapLibre won't auto-repaint when a tile
  // arrives; repaint on every data event so newly-loaded tiles actually show.
  map.on("data", () => { try { map.triggerRepaint(); } catch (e) {} });

  // The pmtiles raster source keeps isStyleLoaded() false, so the render loop can
  // stall before the first paint. Repaint on a short interval during startup until
  // the map goes idle, so it always shows up without needing user interaction.
  const kick = () => { try { map.resize(); map.triggerRepaint(); } catch (e) {} };
  let ticks = 0;
  const startupPaint = setInterval(() => { kick(); if (++ticks > 25) clearInterval(startupPaint); }, 400);
  map.once("idle", () => clearInterval(startupPaint));
  window.addEventListener("resize", kick);
}

function coords(){ const [W,S,E,N] = META.bbox; return [[W,N],[E,N],[E,S],[W,S]]; }

function addOverlays(){
  const c = coords();
  const files = { landcover:"data/landcover.png", heat:"data/heat.png", priority:"data/priority.png" };
  const canopyPM = "pmtiles://" + new URL("data/canopy.pmtiles", location.href).href;
  for (const id of OVERLAY_IDS){
    if (!META.layers[id]) continue;
    if (!map.getSource(id)) {
      if (id === "canopy") map.addSource(id, { type:"raster", url:canopyPM, tileSize:256 });
      else map.addSource(id, { type:"image", url:files[id], coordinates:c });
    }
    if (!map.getLayer(id))
      map.addLayer({ id, type:"raster", source:id,
        paint:{ "raster-opacity":overlayOpacity, "raster-resampling":id==="landcover"?"nearest":"linear", "raster-fade-duration":0 } });
  }
}

function addLabels(){
  if (!map.getSource("labels")) map.addSource("labels", { type:"geojson", data:"data/labels.geojson" });
  const vis = labelActive ? "visible" : "none";
  const nameField = ["case", ["==", ["get", "name_en"], ""], ["get", "name"], ["get", "name_en"]];
  if (!map.getLayer("lbl-place")) map.addLayer({
    id:"lbl-place", type:"symbol", source:"labels", filter:["==",["get","kind"],"place"],
    layout:{ "text-field":nameField, "text-font":["Noto Sans Regular"],
      "text-size":["interpolate",["linear"],["zoom"],11,10,14,13,16,16], "text-padding":6,
      "text-anchor":"center", visibility:vis },
    paint:{ "text-color":"#f2f7f3", "text-halo-color":"#0d1712", "text-halo-width":1.6 } });
  if (!map.getLayer("lbl-poi")) map.addLayer({
    id:"lbl-poi", type:"symbol", source:"labels", filter:["==",["get","kind"],"poi"],
    layout:{ "text-field":nameField, "text-font":["Noto Sans Regular"],
      "text-size":["interpolate",["linear"],["zoom"],12,10,16,13], "text-anchor":"top",
      "text-offset":[0,0.6], visibility:vis },
    paint:{ "text-color":"#f4dca6", "text-halo-color":"#0d1712", "text-halo-width":1.4 } });
  if (!map.getLayer("lbl-road")) map.addLayer({
    id:"lbl-road", type:"symbol", source:"labels", filter:["==",["get","kind"],"road"],
    layout:{ "text-field":nameField, "text-font":["Noto Sans Regular"],
      "text-size":["interpolate",["linear"],["zoom"],13,9,17,12], "text-padding":4, visibility:vis },
    paint:{ "text-color":"#cfe0d6", "text-halo-color":"#0d1712", "text-halo-width":1.3 } });
}

function setLabels(on){
  labelActive = on;
  if (on) addLabels();
  ["lbl-place","lbl-poi","lbl-road"].forEach((id) => { if (map.getLayer(id)) map.setLayoutProperty(id, "visibility", on ? "visible" : "none"); });
}

function refreshLayers(){
  for (const id of OVERLAY_IDS){
    if (!map.getLayer(id)) continue;
    map.setLayoutProperty(id, "visibility", active[id] ? "visible" : "none");
    map.setPaintProperty(id, "raster-opacity", overlayOpacity);
  }
  updateMapLegend();
  nudge();   // the render loop is stalled (pmtiles keeps isStyleLoaded false); force a paint
}

// Force the map to render. Needed because the pmtiles source leaves isStyleLoaded()
// false, so MapLibre's on-demand render loop won't repaint on its own after startup.
let _nudges = 0, _nudgeTimer = null;
function nudge(){
  if (_nudgeTimer) clearInterval(_nudgeTimer);
  _nudges = 0;
  _nudgeTimer = setInterval(() => { try { map.triggerRepaint(); } catch (e) {} if (++_nudges > 20) { clearInterval(_nudgeTimer); _nudgeTimer = null; } }, 200);
}

function buildUI(){
  buildStats();
  buildPriorityCard();
  buildLayerCards();
  buildPointToggles();
  buildTrees();
  buildSources();
  wireBasemap();
  const lt = document.getElementById("labelsToggle");
  if (lt) lt.addEventListener("change", (e) => setLabels(e.target.checked));
  document.getElementById("opacity").addEventListener("input", (e) => {
    overlayOpacity = +e.target.value / 100;
    for (const id of OVERLAY_IDS) if (map.getLayer(id)) map.setPaintProperty(id, "raster-opacity", overlayOpacity);
  });
}

function buildStats(){
  const c = META.layers.canopy, h = META.layers.heat, p = META.layers.priority;
  const rows = [];
  if (p) rows.push({ cls:"plant", n:fmtNum(p.stats.area_ha), l:"hectares of hot, treeless, plantable land" });
  if (c) rows.push({ cls:"green", n:c.stats.tree_cover_pct + "%", l:"of the area under tree canopy (satellite, &ge;3 m)" });
  if (h) rows.push({ cls:"hot", n:Math.round(h.stats.mean_c) + "&deg;C", l:"mean ground temperature, " + fmtDate(h.date) });
  if (h) rows.push({ cls:"hot", n:Math.round(h.stats.max_c) + "&deg;C", l:"hottest surface that day (~50&deg;C air in the heatwave)" });
  document.getElementById("stats").innerHTML = rows.map((r) =>
    `<div class="stat ${r.cls}"><div class="n">${r.n}</div><div class="l">${r.l}</div></div>`).join("");
}

function buildPriorityCard(){
  const P = META.layers.priority;
  const el = document.getElementById("priorityCard");
  if (!P){ el.innerHTML = '<p class="hint">Planting-priority layer not available.</p>'; return; }
  el.innerHTML = `<div class="layer hero" data-layer="priority">
    <div class="top">
      <div><div class="name">Where to plant</div>
        <div class="meta">derived from canopy + land cover + heat &middot; ${P.resolution_m} m</div></div>
      <label class="sw-toggle"><input type="checkbox" data-toggle="priority" checked/>
        <span class="track"><span class="thumb"></span></span></label>
    </div>
    <div class="note">${P.note}</div>
    <div class="legend"><span>helpful</span><span class="bar priority"></span><span>plant first</span></div>
    <div class="legend" style="margin-top:6px"><span style="color:var(--ink-faint)">Hotter, more built-up spots (up to ${P.stats.priority_range_c[1]}&deg;C surface) rank highest.</span></div>
  </div>`;
  el.querySelector('input[data-toggle="priority"]').addEventListener("change", (e) => { active.priority = e.target.checked; refreshLayers(); });
}

function buildLayerCards(){
  const wrap = document.getElementById("layers");
  wrap.innerHTML = ["canopy","heat","landcover"].filter((id) => META.layers[id]).map((id) => {
    const L = META.layers[id];
    return `<div class="layer" data-layer="${id}">
      <div class="top">
        <div><div class="name">${L.title}</div>
          <div class="meta">${L.source.split("(")[0].trim()} &middot; ${L.resolution_m} m</div></div>
        <label class="sw-toggle"><input type="checkbox" data-toggle="${id}" ${active[id]?"checked":""}/>
          <span class="track"><span class="thumb"></span></span></label>
      </div>
      <div class="note">${id === "canopy" ? "Canopy height from an ML model on ~1 m imagery. It reads height, so it separates trees (tall) from grass, crops and parks (low), not by colour. Served as native-resolution tiles - zoom in for individual tree crowns." : L.note}</div>
      ${legendFor(id, L)}
    </div>`;
  }).join("");
  wrap.querySelectorAll("input[data-toggle]").forEach((inp) =>
    inp.addEventListener("change", (e) => { active[e.target.dataset.toggle] = e.target.checked; refreshLayers(); }));
}

function legendFor(id, L){
  if (id === "canopy")
    return `<div class="legend"><span>low</span><span class="bar canopy"></span><span>tall</span>
      <span style="width:100%;color:var(--ink-faint)">canopy height, ~1&ndash;${L.stats.max_height_m} m</span></div>`;
  if (id === "heat")
    return `<div class="legend"><span>${L.stats.display_min_c}&deg;</span><span class="bar heat"></span><span>${L.stats.display_max_c}&deg;C</span></div>`;
  if (id === "landcover"){
    const sw = L.breakdown.filter((b) => b.pct >= 1).map((b) =>
      `<div class="sw"><i style="background:${b.color}"></i>${b.name} ${b.pct}%</div>`).join("");
    return `<div class="swatches">${sw}</div>`;
  }
  return "";
}

async function loadPoints(id){
  if (POINTS[id]) return POINTS[id];
  const buf = await (await fetch(`data/points_${id}.bin`)).arrayBuffer();
  POINTS[id] = new Float32Array(buf);
  return POINTS[id];
}

function pointLayer(id, xy){
  return new deck.ScatterplotLayer({
    id: "pts-" + id,
    data: { length: xy.length / 2, attributes: { getPosition: { value: xy, size: 2 } } },
    getFillColor: POINT_STYLE[id].color,
    radiusUnits: "meters", getRadius: 3, radiusMinPixels: 0.5, radiusMaxPixels: 6,
    stroked: false, opacity: 0.9, parameters: { depthTest: false },
  });
}

function ensureDeck(){
  // Attach the deck.gl overlay only on first use - adding it at startup keeps the
  // map style perpetually "loading" and blocks the initial paint.
  if (!deckOverlay && typeof deck !== "undefined" && deck.MapboxOverlay) {
    deckOverlay = new deck.MapboxOverlay({ interleaved:false, layers:[] });
    map.addControl(deckOverlay);
  }
  return deckOverlay;
}

async function refreshPoints(){
  const ov = ensureDeck();
  if (!ov) return;
  const layers = [];
  for (const id of ["trees", "plant"]) {
    if (pointActive[id]) { try { layers.push(pointLayer(id, await loadPoints(id))); } catch (e) {} }
  }
  ov.setProps({ layers });
}

function buildPointToggles(){
  const el = document.getElementById("pointToggles");
  if (!el) return;
  const pc = META.points || {};
  if (typeof deck === "undefined" || (!pc.trees && !pc.plant)) {
    el.innerHTML = '<p class="hint">Point layers unavailable.</p>';
    return;
  }
  el.innerHTML = ["trees", "plant"].filter((id) => pc[id]).map((id) => {
    const st = POINT_STYLE[id];
    const n = Number(pc[id]).toLocaleString("en-US");
    return `<div class="layer" style="border-left-color:rgb(${st.color[0]},${st.color[1]},${st.color[2]})">
      <div class="top">
        <div><div class="name">${st.label}</div>
          <div class="meta">${n} points &middot; ${st.sub}</div></div>
        <label class="sw-toggle"><input type="checkbox" data-point="${id}"/>
          <span class="track"><span class="thumb"></span></span></label>
      </div>
    </div>`;
  }).join("");
  el.querySelectorAll("input[data-point]").forEach((inp) =>
    inp.addEventListener("change", (e) => { pointActive[e.target.dataset.point] = e.target.checked; refreshPoints(); }));
}

function buildTrees(){
  document.getElementById("trees").innerHTML = TREES.map((t) => {
    const slug = t.cn.toLowerCase();
    const ph = PHOTOS[slug];
    const img = ph ? `<img src="data/trees/${slug}.jpg" alt="${t.cn}" loading="lazy" onerror="this.style.display='none'"/>` : "";
    const credit = ph ? `<div class="credit">Photo: ${ph.credit} &middot; ${ph.license}${ph.source?` &middot; <a href="${ph.source}" target="_blank" rel="noopener">source</a>`:""}</div>` : "";
    return `<div class="tree">${img}
      <div class="body">
        <div class="h"><span class="cn">${t.cn} <span class="ur">${t.ur}</span></span></div>
        <div class="bo">${t.bo}</div>
        <div class="desc">${t.desc}</div>
        <div class="plant"><b>Where:</b> ${t.plant}</div>
        <div class="badges">
          <span class="badge">Water <b>${t.water}</b></span>
          <span class="badge">Shade <b>${t.shade}</b></span>
          <span class="badge">Mature <b>${t.size}</b></span>
        </div>
      </div>${credit}</div>`;
  }).join("");
}

function buildSources(){
  const L = META.layers, lines = [];
  lines.push("<p><b>Data sources</b> (real, openly licensed)</p>");
  if (L.canopy) lines.push(`<p>Canopy height: <a href="https://registry.opendata.aws/dataforgood-fb-forestsv2/" target="_blank" rel="noopener">Meta &amp; WRI CHM v2</a>, ${L.canopy.license}.</p>`);
  if (L.landcover) lines.push(`<p>Land cover: <a href="https://esa-worldcover.org" target="_blank" rel="noopener">ESA WorldCover ${L.landcover.year}</a>, ${L.landcover.license}.</p>`);
  if (L.heat) lines.push(`<p>Surface temperature: <a href="https://planetarycomputer.microsoft.com/dataset/landsat-c2-l2" target="_blank" rel="noopener">Landsat C2 L2</a> (${L.heat.scene}, ${fmtDate(L.heat.date)}), ${L.heat.license}. That day's heatwave pushed Larkana to about 50&deg;C air (<a href="https://www.pakistantoday.com.pk/2026/06/12/interior-sindh-temperatures-top-50c-as-karachi-endures-hot-humid-weather" target="_blank" rel="noopener">report</a>); surfaces read hotter than air.</p>`);
  lines.push(`<p>Basemap: OpenStreetMap, CARTO, Esri. Species photos from Wikimedia Commons (credits on each card).</p>`);
  lines.push(`<p style="margin-top:10px;color:var(--ink-faint)">Built by Dr. Safeer Ali Mirani &middot; <a href="mailto:safeer.ali.mirani@gmail.com">contact</a> &middot; data clipped ${fmtDate(META.generated)}.</p>`);
  document.getElementById("sources").innerHTML = lines.join("");
}

function wireBasemap(){
  document.querySelectorAll("#basemap button").forEach((btn) =>
    btn.addEventListener("click", () => {
      document.querySelectorAll("#basemap button").forEach((b) => b.classList.remove("on"));
      btn.classList.add("on");
      map.setStyle(baseStyle(btn.dataset.base));
    }));
}

function updateMapLegend(){
  const el = document.getElementById("maplegend");
  const on = OVERLAY_IDS.filter((id) => active[id]);
  if (!on.length){ el.classList.remove("show"); return; }
  const id = on[on.length - 1];
  const L = META.layers[id];
  let body = "";
  if (id === "priority") body = `<span class="bar priority" style="display:block;width:100%;margin:4px 0"></span>Hot, treeless, plantable land. Magenta = plant first.`;
  if (id === "canopy") body = `<span class="bar canopy" style="display:block;width:100%;margin:4px 0"></span>Low to tall canopy (~1&ndash;${L.stats.max_height_m} m)`;
  if (id === "heat") body = `<span class="bar heat" style="display:block;width:100%;margin:4px 0"></span>${L.stats.display_min_c}&deg; to ${L.stats.display_max_c}&deg;C surface, ${fmtDate(L.date)}`;
  if (id === "landcover") body = L.breakdown.filter((b) => b.pct >= 3).map((b) => `<span style="display:inline-block;width:9px;height:9px;border-radius:2px;background:${b.color};margin-right:5px"></span>${b.name}`).join("<br>");
  el.innerHTML = `<div class="lt">${L.title}</div>${body}`;
  el.classList.add("show");
}

function fmtNum(n){ return (n == null ? "-" : Number(n).toLocaleString("en-US")); }
function fmtDate(iso){
  if (!iso) return "";
  return new Date(iso + "T00:00:00Z").toLocaleDateString("en-GB", { day:"numeric", month:"short", year:"numeric", timeZone:"UTC" });
}
