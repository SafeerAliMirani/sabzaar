# Sabzaar — Project Brief (kickoff for a fresh chat)

> Working name: **Sabzaar** (سبزار), roughly "a place of greenery" (sabz = green). Distinctive, locally rooted, and a July 2026 web search found no notable brand or trademark using it. Alternate if needed: Sayabaan (canopy/shade). Do a proper trademark search before any official or commercial launch.

This is the handoff doc for project 5. Open a new chat with the same folder mounted and say "read Sabzaar/PROJECT_BRIEF.md", and we continue from here. Memory also carries who I am and the plan across chats.

## What it is
A long-term platform to help green Larkana and cut its summer heat, by mapping the city's real tree and green cover and guiding plus tracking the planting of the right (native) trees. A personal project by Dr. Safeer Ali Mirani.

## The problem
- Larkana (Sindh, Pakistan) gets brutally hot in summer (45C and above). The heat is made worse by tree loss: older trees along roads, canals, and in homes were removed for road-widening and construction, and the city was far greener in the past.
- Most replacement planting is Conocarpus, which has real drawbacks: very high water uptake that lowers the water table, aggressive roots that damage pavements and sewer lines, allergenic pollen linked to respiratory problems, non-native with little value for native birds or soil, and thin shade.
- Better choices are native, heat-tolerant shade trees: neem, peepal, banyan, amaltas, jamun, arjun, siris, ber.

## The cause
Help people, nature, birds, air, and cooling by growing more of the right trees. Long-term and low-pressure: Safeer is unsure how much ongoing time he can give, so the design must not depend on his constant involvement.

## Honest verdict (from an Opus strategy review; Fable kept mis-flagging the topic as a safeguard false positive)
Good idea on both counts: a real, worthwhile cause, and a strong portfolio piece that shows geospatial, AI, and full-stack range next to the four WebGPU projects. The one honest caveat is the community side (logging, photos, moderation, leaderboard): it needs ongoing stewardship and fades without a caretaker, so the project must not depend on it.

## Phasing (built around uncertain time)
- **Phase 1, the MVP (build first): a self-sufficient, low-maintenance, high-value core.** A web map of Larkana's real tree and green cover from satellite data, a clear heat-and-greenery view, and practical native-species planting guidance (where to plant, which trees, why not Conocarpus). No backend, no babysitting; it keeps helping even if Safeer steps away.
- **Phase 2, community (only when there is capacity):** residents log trees they plant (type, age, photo, location), a contributions map, and a leaderboard. Needs a backend, auth, photo storage, and moderation.
- **Phase 3, long-term:** history and trends over time, partnerships, expansion beyond Larkana.

## Phase 1 MVP, crisp definition
A single deployable web app that:
1. Shows a map centered on Larkana with a real satellite or tile basemap.
2. Overlays real tree canopy / green-cover data so you can see where it is green versus bare.
3. Optionally overlays a heat signal (land surface temperature) to show the heat-and-greenery link.
4. Offers a "which tree to plant" guide: a curated list of native, heat-tolerant shade trees for Larkana (name, benefits, water need, shade, why better than Conocarpus).
5. Labels clearly what is real satellite data versus editorial guidance.

## Real data (categories verified; the new chat should re-confirm exact current sources and licences, then clip to Larkana's bounding box)
- Basemap / imagery: Sentinel-2 (ESA, 10 m, free) recent true-color, or an attributed tile basemap.
- Tree canopy / land cover (real): ESA WorldCover (10 m, free); Meta and WRI global canopy height (about 1 m, open); Global Forest Watch / Hansen tree cover; OpenStreetMap individual trees (natural=tree) and parks.
- Heat: Landsat or MODIS land surface temperature, or ERA5 for air temperature.
- Honest AI note: remote sensing can reliably estimate canopy cover and detect tree crowns (for example DeepForest on high-res RGB). It cannot reliably give tree species or age from satellite. Species and age come from people on the ground in Phase 2. Do not claim species or age from satellite.

## Tech stack
- Phase 1: static front-end. Map with MapLibre GL JS (open, no token) or Leaflet. Data as pre-processed GeoJSON, raster tiles, or PMTiles, bundled or from open tile services. A small offline Python step (like the convert.py used in the other projects) can fetch and clip Larkana's canopy and heat data. Deploy on Netlify, same pattern as the other four.
- Phase 2: add Supabase (Postgres, auth, storage) or Firebase; contribution markers on the map; image upload with moderation.
- WebGPU angle (optional, do not force it): Safeer's strength could power a striking canopy or heat visualization layer as a highlight, but MapLibre should handle the core map.

## Honest framing (three tiers, kept visually and textually separate)
1. Real satellite / remote-sensing data (canopy, heat): measured.
2. Resident-submitted logs (Phase 2): user-generated, unverified.
3. Editorial guidance (which and where to plant): curated recommendation.
Never blur these. This separation is what keeps the project credible.

## Constraints and values (carry over from the other projects)
- Real public data only, never synthetic.
- Honest framing, no overclaiming.
- Clean and deployable; Phase 1 should be low-maintenance.
- Concise, direct communication; no em-dashes or AI-tells in code or copy.
- Deploy pattern: public GitHub repo plus Netlify auto-deploy. Git creds are cached on the Windows machine (use the Windows PowerShell tool for git, Chrome for the GitHub and Netlify web steps).

## First steps in the new chat
1. De-risk the data: confirm we can actually get Larkana's canopy plus a basemap into a browser map (pick exact sources, clip to Larkana's bounding box).
2. Scaffold the map app (MapLibre centered on Larkana with a basemap).
3. Add the real canopy layer and the native-tree planting guide.
4. Honest framing, then deploy.
