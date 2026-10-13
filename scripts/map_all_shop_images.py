import json
import os
import urllib.request
from pathlib import Path

# Load API items
req = urllib.request.Request('https://api.deadlock-api.com/v1/assets/items?language=english', headers={'User-Agent': 'Mozilla/5.0'})
with urllib.request.urlopen(req, timeout=30) as resp:
    api_items = json.load(resp)

# Load local items
with open('data/items.json', 'r', encoding='utf-8') as f:
    local_items = json.load(f)

# Build lookup tables for API items that HAVE shop_image_webp
by_class = {}
by_name_en = {}
by_clean_stem = {}

for it in api_items:
    shop_url = it.get('shop_image_webp')
    if not shop_url or 'panorama:' in shop_url:
        continue
    cname = it.get('class_name', '')
    by_class[cname] = (it, shop_url)
    
    name = (it.get('name') or '').strip().lower()
    if name:
        by_name_en[name] = (it, shop_url)
    
    stem = Path(shop_url).stem.lower()
    by_clean_stem[stem] = (it, shop_url)
    by_clean_stem[stem.replace('_', '-')] = (it, shop_url)

# Aliases for items whose local slug or name doesn't match API class/stem directly
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

mapped = {}
unmapped = []

for it in local_items:
    slug = it["id"]
    res = None
    
    # 1. Manual mapping
    if slug in MANUAL_MAPPING:
        target_cname = MANUAL_MAPPING[slug]
        if target_cname in by_class:
            res = by_class[target_cname]
    
    # 2. Direct class name match
    if not res:
        for cname in [f"upgrade_{slug.replace('-', '_')}", f"ability_{slug.replace('-', '_')}", slug.replace('-', '_')]:
            if cname in by_class:
                res = by_class[cname]
                break
                
    # 3. Stem match
    if not res:
        for s in [slug, slug.replace('-', '_')]:
            if s in by_clean_stem:
                res = by_clean_stem[s]
                break

    # 4. Name EN match
    if not res and it.get("name_en"):
        n = it["name_en"].strip().lower()
        if n in by_name_en:
            res = by_name_en[n]
            
    if res:
        api_obj, shop_url = res
        mapped[slug] = (api_obj, shop_url)
    else:
        unmapped.append(it)

print(f"Mapped: {len(mapped)} / {len(local_items)}")
print(f"Unmapped: {len(unmapped)}")
for u in unmapped:
    print(f"  UNMAPPED: {u['id']:30} | {u.get('name_en'):30} | {u.get('category')}")
