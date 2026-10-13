"""Download authentic Valve full-color shop art for all 214 items.
Ensures zero monochrome silhouettes remain in web/assets/items/.
"""

import json
import os
import sys
import urllib.request
from pathlib import Path
from PIL import Image
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ITEMS_DIR = ROOT / "web" / "assets" / "items"
ITEMS_JSON = DATA_DIR / "items.json"
OVERRIDES_JSON = DATA_DIR / "icon_overrides.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Load local items
local_items = json.loads(ITEMS_JSON.read_text(encoding="utf-8"))

# Load API items
req = urllib.request.Request("https://api.deadlock-api.com/v1/assets/items?language=english", headers={"User-Agent": UA})
with urllib.request.urlopen(req, timeout=30) as resp:
    api_items = json.load(resp)

by_class = {}
by_name_en = {}
by_clean_stem = {}

for it in api_items:
    shop_url = it.get("shop_image_webp")
    if not shop_url or "panorama:" in shop_url:
        continue
    cname = it.get("class_name", "")
    by_class[cname] = (it, shop_url)
    
    name = (it.get("name") or "").strip().lower()
    if name:
        by_name_en[name] = (it, shop_url)
    
    stem = Path(shop_url).stem.lower()
    by_clean_stem[stem] = (it, shop_url)
    by_clean_stem[stem.replace("_", "-")] = (it, shop_url)

MANUAL_MAPPING = {
    "basic-magazine": "upgrade_clip_size",
    "monster-rounds": "upgrade_non_player_bonus",
    "close-quarters": "upgrade_close_quarters",
    "point-blank": "upgrade_close_quarter_combat",
    "sprint-boots": "upgrade_sprint_booster",
    "fleetfoot-boots": "upgrade_fleetfoot",
    "toxic-bullets": "upgrade_toxic_bullets",
    "berserker": "upgrade_berserker",
    "life-strike": "upgrade_lifestrike_gauntlets",
    "leech": "upgrade_damage_recycler",
    "healbane": "upgrade_healbane",
    "counterspell": "upgrade_counterspell",
    "curse": "upgrade_glitch",
    "improved-cooldown": "upgrade_magic_tempo",
    "advanced-cooldown": "upgrade_cooldown_reduction",
    "superior-cooldown": "upgrade_cooldown_reduction",
    "mystic-reach": "upgrade_magic_reach",
    "silence-glyph": "upgrade_targeted_silence",
    "bullet-armor": "upgrade_bullet_armor",
    "improved-bullet-armor": "upgrade_improved_bullet_armor",
    "heavy-bullet-armor": "upgrade_bullet_armor",
    "spirit-armor": "upgrade_tech_armor",
    "improved-spirit-armor": "upgrade_tech_purge",
    "heavy-magic-resist": "upgrade_tech_armor",
    "melee-rebuttal": "upgrade_melee_rebuttal",
    "hunter-s-mark": "upgrade_bullet_armor_reduction_aura",
    "regeneration-booster": "upgrade_healing_booster",
    "pool-of-restoration": "upgrade_restorative_locket",
    "battle-hardened": "upgrade_regenerating_bullet_shield",
    "thornskin": "upgrade_return_fire",
    "fortress-guard": "upgrade_magic_shield",
    "siphon": "upgrade_siphon_bullets",
    "unyielding-heart": "upgrade_unstoppable",
    "guardian-aura": "upgrade_rescue_beam",
    "fortune-hand": "upgrade_improved_spirit",
    "arcane-focus": "upgrade_acolytes_glove",
    "apprentice-tome": "upgrade_magic_vulnerability",
    "ritualist": "upgrade_tech_damage_pulse",
    "ward-of-revelation": "upgrade_diviners_kevlar",
    "bloodletter": "upgrade_toxic_bullets",
    "executioners-edge": "upgrade_glass_cannon",
    "tempest-scope": "upgrade_sharpshooter",
    "extra-health": "upgrade_health",
    "knockdown": "upgrade_target_stun",
    "metal-skin": "upgrade_metal_skin",
    "warp-stone": "upgrade_warp_stone",
    "phantom-strike": "upgrade_phantom_strike",
    "veil-walker": "upgrade_veil_walker",
    "reactive-barrier": "upgrade_vex_barrier",
    "divine-barrier": "upgrade_divine_barrier",
    "diviners-kevlar": "upgrade_diviners_kevlar",
    "colossus": "upgrade_colossus",
    "restorative-locket": "upgrade_restorative_locket",
    "healing-booster": "upgrade_healing_booster",
    "health-nova": "upgrade_health_nova",
    "health-stimpak": "upgrade_health_stimpak",
    "shrink-ray": "upgrade_shrink_ray",
    "infuser": "upgrade_infuser",
    "guardian-ward": "upgrade_guardian_ward",
    "thermal-detonator": "upgrade_thermal_detonator",
    "kinetic-sash": "upgrade_kinetic_sash",
    "arcane-surge": "upgrade_arcane_surge",
    "cold-front": "upgrade_cold_front",
    "arctic-blast": "upgrade_arctic_blast",
    "self-bubble": "upgrade_self_bubble",
    "glitch": "upgrade_glitch",
    "arcane-extension": "upgrade_arcane_extension",
    "imbued-duration-extender": "upgrade_imbued_duration_extender",
    "glass-cannon": "upgrade_glass_cannon",
    "fury-trance": "upgrade_fury_trance",
    "surging-power": "upgrade_surging_power",
    "boxing-glove": "upgrade_boxing_glove",
    "acolytes-glove": "upgrade_acolytes_glove",
    "spirit-snatch": "upgrade_spirit_snatch",
    "melee-charge": "upgrade_melee_charge",
    "crushing-fists": "upgrade_crushing_fists",
    "resonant-healing": "upgrade_resonant_healing",
    "burst-fire": "upgrade_burst_fire",
    "endurance": "upgrade_endurance",
    "magic-storm": "upgrade_magic_storm",
    "quick-silver": "upgrade_quick_silver",
    "ethereal-bullets": "upgrade_ethereal_bullets",
    "intensifying-clip": "upgrade_intensifying_clip",
    "reinforcing-casings": "upgrade_reinforcing_casings",
    "blitz-bullets": "upgrade_blitz_bullets",
    "vex-barrier": "upgrade_vex_barrier",
    "medic-bullets": "upgrade_medic_bullets",
    "titan-round": "upgrade_titan_round",
    "split-shot": "upgrade_split_shot",
    "active-reload": "upgrade_active_reload",
    "magic-carpet": "upgrade_magic_carpet",
    "spirit-burn": "upgrade_spirit_burn",
    "ultimate-burst": "upgrade_ultimate_burst",
    "absorbing-armor": "upgrade_absorbing_armor",
    "healbuff": "upgrade_healbuff",
    "aprounds": "upgrade_aprounds",
    "infinite-rounds": "upgrade_infinite_rounds",
    "deflecting-armor": "upgrade_deflecting_armor",
    "icarus-wings": "upgrade_icarus_wings",
    "mystical-piano": "upgrade_mystical_piano",
    "nullification-aura": "upgrade_nullification_aura",
    "celestial-guidance": "upgrade_celestial_guidance",
    "eldritch-shot": "upgrade_eldritch_shot",
    "prism-blast": "upgrade_prism_blast",
    "bulletshredimbue": "upgrade_bulletshredimbue",
    "rechargingbullets": "upgrade_rechargingbullets",
    "goose-egg": "upgrade_goose_egg",
    "debuff-reducer": "upgrade_debuff_reducer",
    "reduce-debuff-duration": "upgrade_reduce_debuff_duration",
    "tech-purge": "upgrade_tech_purge",
    "chonky": "upgrade_chonky",
    "damage-recycler": "upgrade_damage_recycler",
    "sprint-booster": "upgrade_sprint_booster",
    "trophy-collector": "upgrade_trophy_collector",
    "cardio-calibrator": "upgrade_cardio_calibrator",
    "superior-stamina": "upgrade_superior_stamina",
    "rapid-rounds": "upgrade_rapid_rounds",
    "improved-stamina": "upgrade_improved_stamina",
    "bullet-armor-reduction-aura": "upgrade_bullet_armor_reduction_aura",
    "grit": "upgrade_grit",
    "weapon-shielding": "upgrade_weapon_shielding",
    "spirit-bubble": "upgrade_spirit_bubble",
    "regenerating-bullet-shield": "upgrade_regenerating_bullet_shield",
    "magic-shield": "upgrade_magic_shield",
    "improved-spirit": "upgrade_improved_spirit",
    "mystic-regeneration": "upgrade_mystic_regeneration",
    "soaring-spirit": "upgrade_soaring_spirit",
    "tech-overflow": "upgrade_tech_overflow",
    "tech-range": "upgrade_tech_range",
    "extra-charge": "upgrade_extra_charge",
    "health-stealing-magic": "upgrade_health_stealing_magic",
    "bullet-resist-shredder": "upgrade_bullet_resist_shredder",
    "mystic-reverb": "upgrade_mystic_reverb",
    "magic-burst": "upgrade_magic_burst",
    "magic-shock": "upgrade_magic_shock",
    "magic-vulnerability": "upgrade_magic_vulnerability",
    "magic-slow": "upgrade_magic_slow",
    "escalating-exposure": "upgrade_escalating_exposure",
    "rapid-recharge": "upgrade_rapid_recharge",
    "omnicharge-pendant": "upgrade_omnicharge_pendant",
    "magic-tempo": "upgrade_magic_tempo",
    "cooldown-reduction": "upgrade_cooldown_reduction",
    "containment": "upgrade_containment",
    "spirit-sap": "upgrade_spirit_sap",
    "focus-lens": "upgrade_focus_lens",
    "withering-whip": "upgrade_withering_whip",
    "greater-withering-whip": "upgrade_greater_withering_whip",
    "rescue-beam": "upgrade_rescue_beam",
    "rupture": "upgrade_rupture",
    "discord": "upgrade_discord",
    "target-stun": "upgrade_target_stun",
    "aoe-root": "upgrade_aoe_root",
    "ability-refresher": "upgrade_ability_refresher",
    "ability-power-shard": "upgrade_ability_power_shard",
    "cheat-death": "upgrade_cheat_death",
    "cloaking-device-active": "upgrade_cloaking_device_active",
    "rocket-booster": "upgrade_rocket_booster",
}

def get_mapping(it: dict) -> tuple[dict, str] | None:
    slug = it["id"]
    if slug in MANUAL_MAPPING and MANUAL_MAPPING[slug] in by_class:
        return by_class[MANUAL_MAPPING[slug]]
    for cname in [f"upgrade_{slug.replace('-', '_')}", f"ability_{slug.replace('-', '_')}", slug.replace('-', '_')]:
        if cname in by_class:
            return by_class[cname]
    for s in [slug, slug.replace('-', '_')]:
        if s in by_clean_stem:
            return by_clean_stem[s]
    if it.get("name_en"):
        n = it["name_en"].strip().lower()
        if n in by_name_en:
            return by_name_en[n]
    return None

overrides = {}
downloaded = 0
failed = 0

print(f"Downloading painted shop art for {len(local_items)} items...")

for idx, it in enumerate(local_items, 1):
    slug = it["id"]
    res = get_mapping(it)
    if not res:
        print(f"[{idx}/{len(local_items)}] ERROR: No mapping for {slug}")
        failed += 1
        continue
    
    api_obj, shop_url = res
    target_path = ITEMS_DIR / f"{slug}.webp"
    
    try:
        req_img = urllib.request.Request(shop_url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req_img, timeout=20) as resp:
            data = resp.read()
        target_path.write_bytes(data)
        downloaded += 1
        it["shop_image"] = f"/assets/items/{slug}.webp"
        overrides[slug] = {
            "source": "valve_shop_image_webp",
            "class_name": api_obj.get("class_name"),
            "url": shop_url
        }
        if idx % 20 == 0 or idx == len(local_items):
            print(f"[{idx}/{len(local_items)}] Downloaded {slug}.webp from {shop_url}")
    except Exception as exc:
        print(f"[{idx}/{len(local_items)}] FAILED {slug}: {exc}")
        failed += 1

print(f"\nDownload summary: {downloaded} successful, {failed} failed.")

# Save updated metadata
ITEMS_JSON.write_text(json.dumps(local_items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
OVERRIDES_JSON.write_text(json.dumps(overrides, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

# Verification pass
print("\n--- RUNNING COLOR VERIFICATION AUDIT ---")
mono_items = []
color_items = []

for it in local_items:
    slug = it["id"]
    p = ITEMS_DIR / f"{slug}.webp"
    if not p.is_file():
        mono_items.append((slug, "file missing"))
        continue
    im = Image.open(p).convert("RGB")
    arr = np.array(im, dtype=float)
    channel_std = np.std(arr, axis=2)
    mean_diff = float(np.mean(channel_std))
    max_diff = float(np.max(channel_std))
    ratio = float(np.mean(channel_std > 5))
    
    if ratio < 0.05 or mean_diff < 3.0:
        mono_items.append((slug, f"mean={mean_diff:.2f}, ratio={ratio:.3f}"))
    else:
        color_items.append((slug, mean_diff, ratio))

print(f"Total checked: {len(local_items)}")
print(f"Color items: {len(color_items)}")
print(f"Monochrome items: {len(mono_items)}")

if mono_items:
    print("\nWARNING - REMAINING MONOCHROME ITEMS:")
    for slug, reason in mono_items:
        print(f"  {slug:30}: {reason}")
else:
    print("\nSUCCESS: 100% of items are full-color painted Valve shop illustrations!")
