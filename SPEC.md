# DeadlockForge — Specification

Status: ready-for-agent

## Problem Statement

Player of Valve Deadlock (map The Cursed Apple, 3 lanes) needs to prepare for a match before/while playing: know the full hero and item roster, understand what the enemy 6-hero team threatens, pick the right situational counter-items at the right tier and timing, sandbox a 16-slot build and see its stats instantly, and refresh lane/macro knowledge of the current 3-lane map. Today this knowledge is scattered across wikis, videos and spreadsheets, and none of it recalculates a build's stats live.

## Solution

A local, self-contained tactical web tool: a FastAPI server serving a dark noir/steampunk single-page application. It ships a validated dataset of 22 heroes and the full item catalog (Weapon/Vitality/Spirit, Tiers 1-4), a Counter-Item Advisor that turns "my hero + up to 6 enemies" into prioritized counter-buy recommendations with explanations, tier and timing, a Build Lab with 12 category slots + 4 flex slots and a live stat calculator, and a map guide for York/Broadway/Greenwich with an event timeline. No game integration, no input emulation — purely an autonomous web tool.

## User Stories

1. As a Deadlock player, I want to browse all 22 heroes with base stats, roles and signature threats, so that I can plan picks and lane assignments.
2. As a player, I want to filter heroes by role, so that I can quickly find a Carry, Support or Initiator.
3. As a player, I want to select my hero and up to 6 enemy heroes, so that the tool understands my matchup.
4. As a player, I want the advisor to name the 2-3 key threats of the enemy setup, so that I know what I am buying against.
5. As a player, I want prioritized counter-item recommendations, so that I know what to buy first.
6. As a player, I want each recommendation to carry a human-readable explanation, so that I understand why the item counters the threat.
7. As a player, I want the tier (and soul cost) of each recommended item, so that I can time my economy.
8. As a player, I want an optimal buy timing for each recommendation, so that I know when in the game it matters.
9. As a player, I want recommendations against heal-heavy lineups (Abrams, Kelvin, Infernus), so that I can counter sustain with Healbane / Toxic Bullets.
10. As a player, I want recommendations against uninterruptible ultimates (Seven Storm, McGinnis), so that I can punish them with Knockdown / Curse.
11. As a player, I want recommendations against rapid bullet-DPS (Haze, Wraith), so that I can survive with Metal Skin / Return Fire.
12. As a player, I want recommendations against spirit casters (Pocket, Yamato, Grey Talon), so that I can shut them down with Silence Glyph / Spellbreaker / Spirit Armor.
13. As a player, I want to click an item in the catalog and have it equip into the correct category slot, so that assembling a build is one gesture.
14. As a player, I want the 4 Weapon (orange), 4 Vitality (green), 4 Spirit (purple) and 4 flex slots, so that my loadout mirrors the game's 16-slot board.
15. As a player, I want flex slots to accept items of any category, so that I can run hybrid builds.
16. As a player, I want category slots to reject items from another category, so that the tool guards me against invalid builds.
17. As a player, I want Weapon DPS, Bullet Shield, Spirit Shield, Max HP, Stamina, Cooldown Reduction and Spirit Power to recalculate in real time, so that I can compare builds instantly.
18. As a player, I want to search items by name, so that I can find one item among dozens fast.
19. As a player, I want to filter items by cost tier, so that I can window my shopping to my current souls.
20. As a player, I want to filter items by category, so that I can browse one color of the shop.
21. As a player, I want item tooltips with effect descriptions, so that I can decide without leaving the app.
22. As a player, I want a reset button, so that I can start a fresh build in one click.
23. As a player, I want to see a diagram of the 3-lane map, so that I can orient on York/Broadway/Greenwich.
24. As a player, I want per-lane notes (districts, Midboss lair), so that I understand each flank's character.
25. As a player, I want the 2-2-2 lane assignment and roaming guidance, so that I can macro correctly.
26. As a player, I want the event timeline (0:00 start, 3:00 jungle camps, 10:00 Soul Urn + flex slots, Midboss phases), so that I can plan objectives.
27. As a QA engineer, I want dataset validation tests, so that corrupted or incomplete data fails loudly instead of silently.
28. As a QA engineer, I want calculator math tests with hand-computed cases, so that the stat engine is provably correct.
29. As a QA engineer, I want advisor correctness tests, so that known threat signatures always yield the expected counter items.
30. As a developer, I want deep modules with small interfaces, so that the engine can be extended with new heroes/items without touching callers.
31. As a developer, I want the dataset in JSON validated by Pydantic schemas, so that data drift breaks tests, not production behavior.
32. As a user, I want the server to run locally with one command, so that I can start the tool without setup friction.
33. As a user, I want the app to respond quickly and work on a narrow window, so that I can keep it open beside the game.

## Implementation Decisions

- Deep-module layout: `dataset` (load+validate), `advisor`, `calculator`, `builds` (slot validation) are four modules behind small interfaces; the FastAPI layer is a thin adapter over them. Each module tested through its public interface only.
- Single seam for the whole frontend: REST endpoints `/api/heroes`, `/api/items`, `/api/advice`, `/api/calc`, `/api/map`, `/api/build/validate`. The SPA talks only to this seam.
- Data lives in JSON files under `data/` (heroes, items, threats, map); validated by Pydantic models at load time. SQLite is available via a small persistence adapter for saved builds, but the working schema is JSON.
- Hero model carries `threat_tags`: a closed vocabulary (e.g. `heal`, `uninterruptible_ult`, `bullet_dps`, `spirit_caster`, `burst`, `mobility`, `silence_vulnerable`) that the advisor scores; signature abilities map to tags.
- Item model carries `category` (weapon/vitality/spirit), `tier` 1-4 with cost bands (500 / 1250 / 3000 / 6200+), `stats` dict (weapon_damage, fire_rate, base_hp, bullet_armor, spirit_armor, stamina, cooldown_reduction, spirit_power), `active`/`passive` descriptions, and an optional `counters` list of threat tags it addresses.
- Advisor: threat score = weighted sum over enemy heroes' tags (weights per tag, +bonus for repeated tags across heroes — stacking the same threat amplifies it). Recommendations ranked by (coverage of top threat, tier ascending). Each recommendation includes explanation string, tier, soul cost, timing hint (tier-based: T1 early ~0:00-8:00, T2 mid ~8:00-15:00, T3 ~15:00-22:00, T4 late).
- Calculator: aggregates item stats additively with multiplicative modifiers for fire_rate, cooldown_reduction (capped at 55%), returning a stats snapshot; DPS = weapon_damage × fire_rate × hit_factor, shields use armor-style resist formula.
- Build slots: 12 category slots + 4 flex; category slot accepts only its category, flex accepts all. Same item cannot occupy two slots.
- UI: single `index.html`, Tailwind (CDN, no build step), ES modules in `web/js/`. Dark noir/steampunk: near-black background, sepia/gold accent, category colors orange/emerald/purple. Three views (Build Lab, Matchup Advisor, Map Guide) via tab navigation.
- Server: FastAPI + Uvicorn, serves static SPA and API on one port. Python 3.14, FastAPI 0.141, Pydantic 2.
- Language of UI: Russian.
- No game process control, no input emulation, no external services.

## Testing Decisions

- Good tests cross the module interfaces only, never internals: advisor tested via its recommend() function with enemy rosters; calculator via compute() with item lists; dataset via loader assertions.
- Dataset integrity: every hero has required numeric ranges (HP>0 etc.), every item has a known category/tier/cost, every item's `counters` tags exist in the threat vocabulary, costs match tier bands, no duplicate ids.
- Calculator math: hand-computed cases (e.g. 4 items each +50 weapon damage → expected totals; cooldown reduction capping at 55%).
- Advisor: enemy team with Abrams+Kelvin+Infernus → top recommendations include an anti-heal item; Seven+McGinnis → Knockdown/Curse present; Haze+Wraith → Metal Skin/Return Fire; Pocket+Yamato+Grey Talon → anti-caster items.
- Tests run via pytest; all green before delivery.

## Out of Scope

- In-game overlay, memory reading, input emulation, game launch automation.
- Match replay parsing, telemetry, replay import.
- Multiplayer/shared builds, accounts, cloud sync.
- Real-time game state sync.
- Hero ability-level theorycrafting (per-ability damage simulation).

## Further Notes

- Item costs/tiers reflect public knowledge of the shop categories and tier bands; the dataset is a curated snapshot, not live game data.
- Follow the deep-module discipline from `codebase-design` skill: dataset/advisor/calculator are the load-bearing modules; the API layer is a thin adapter.
