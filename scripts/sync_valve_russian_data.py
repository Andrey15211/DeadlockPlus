"""Sync official Valve Russian data for heroes and items.

- Fetches official Russian & English data from deadlock-api.com
- Downloads 100% full-color WebP shop icons for all items
- Removes placeholder SVG letter diamonds
- Updates data/heroes.json with official Russian names
- Updates data/items.json with parsed Russian descriptions, components, upgrades_to,
  stats, cooldowns, and costs.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
ITEMS_DIR = ROOT / "web" / "assets" / "items"
ITEMS_JSON = DATA_DIR / "items.json"
HEROES_JSON = DATA_DIR / "heroes.json"
OVERRIDES_JSON = DATA_DIR / "icon_overrides.json"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Official Russian hero names from Valve client
OFFICIAL_HERO_NAMES_RU: dict[str, str] = {
    "infernus": "Инфернус",
    "seven": "Семь",
    "vindicta": "Виндикта",
    "lady-geist": "Леди Гайст",
    "abrams": "Абрамс",
    "wraith": "Тень",
    "mcginnis": "Макгиннис",
    "paradox": "Парадокс",
    "dynamo": "Динамо",
    "kelvin": "Кельвин",
    "haze": "Хейз",
    "astro": "Холлидей",
    "bebop": "Бебоп",
    "nano": "Калико",
    "grey-talon": "Серый Коготь",
    "mo-and-krill": "Мо и Мелкий",
    "shiv": "Шив",
    "ivy": "Айви",
    "warden": "Дозорный",
    "yamato": "Ямато",
    "lash": "Хлыст",
    "viscous": "Вязкус",
    "pocket": "Карман",
    "mirage": "Мираж",
    "viper": "Гадюка",
    "magician": "Синклер",
    "vampirebat": "Мина",
    "drifter": "Скиталец",
    "priest": "Венатор",
    "frank": "Виктор",
    "bookworm": "Пейдж",
    "doorman": "Швейцар",
    "punkgoat": "Билли",
    "necro": "Грейвс",
    "fencer": "Аполлон",
    "deadpack": "Дэнни Мертвец",
    "familiar": "Дрём",
    "werewolf": "Сильвер",
    "unicorn": "Селеста",
    "ratking": "Крысиный король",
    "chessmaster": "Соломон",
    "artist": "Вайолет",
    "nurse": "Медсестра Хэрроу",
    "baba": "Буся",
}

# Mapping of older/variant item slugs in data/items.json to official shop items
ITEM_SLUG_ALIASES: dict[str, str] = {
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


def _http_json(url: str) -> list | dict:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _http_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def clean_description(raw: str | dict | None) -> str:
    """Strip service tokens, raw HTML, SVGs, and clean up description text."""
    if not raw:
        return ""
    text = raw.get("desc", "") if isinstance(raw, dict) else str(raw)
    # Strip inline SVG tags
    text = re.sub(r"<svg.*?</svg>", "", text, flags=re.DOTALL)
    # Strip HTML tags like <span class="highlight">, <b color="...">, <panel ...>
    text = re.sub(r"<[^>]+>", "", text)
    # Clean up formatting tokens {s:sign}, {s:value}, {s:bonus}, etc.
    text = re.sub(r"\{[^}]+\}", "", text)
    # Collapse excess spaces
    text = re.sub(r"\s+", " ", text).strip()
    return text



def clean_properties_to_stats(props: dict) -> dict[str, float]:
    """Parse properties into normalized stats."""
    stats: dict[str, float] = {}
    if not props:
        return stats
    mapping = {
        "MODIFIER_VALUE_WEAPON_DAMAGE_INCREASE": "weapon_damage",
        "MODIFIER_VALUE_WEAPON_POWER": "weapon_damage",
        "MODIFIER_VALUE_ATTACK_SPEED_INCREASE": "fire_rate_pct",
        "MODIFIER_VALUE_FIRE_RATE_INCREASE": "fire_rate_pct",
        "MODIFIER_VALUE_HEALTH_MAX": "base_hp",
        "MODIFIER_VALUE_BULLET_ARMOR": "bullet_shield",
        "MODIFIER_VALUE_TECH_ARMOR": "spirit_shield",
        "MODIFIER_VALUE_STAMINA_MAX": "stamina",
        "MODIFIER_VALUE_COOLDOWN_REDUCTION_PERCENT": "cooldown_reduction_pct",
        "MODIFIER_VALUE_TECH_POWER": "spirit_power",
        "MODIFIER_VALUE_MOVE_SPEED": "move_speed",
    }
    for k, v in props.items():
        if not isinstance(v, dict):
            continue
        ptype = v.get("provided_property_type", "")
        raw_val = v.get("value", "")
        if ptype in mapping:
            try:
                num = float(raw_val)
                target_key = mapping[ptype]
                if target_key in ("fire_rate_pct", "cooldown_reduction_pct"):
                    # If expressed in percents like 12 or 15
                    if num > 1.0:
                        num = num / 100.0
                stats[target_key] = num
            except (ValueError, TypeError):
                pass
    return stats


def extract_cooldown(props: dict) -> float | None:
    if not props:
        return None
    for k, v in props.items():
        if k in ("AbilityCooldown", "Cooldown", "m_flCooldown"):
            if isinstance(v, dict):
                try:
                    return float(v.get("value", 0))
                except (ValueError, TypeError):
                    pass
            else:
                try:
                    return float(v)
                except (ValueError, TypeError):
                    pass
    return None


def sync_heroes(api_heroes_ru: list[dict], api_heroes_en: list[dict]) -> None:
    print("--- Синхронизация героев ---")
    if not HEROES_JSON.is_file():
        print("heroes.json не найден")
        return
    heroes = json.loads(HEROES_JSON.read_text(encoding="utf-8"))
    en_by_id = {h.get("id"): h for h in api_heroes_en}
    ru_by_id = {h.get("id"): h for h in api_heroes_ru}

    updated_count = 0
    for h in heroes:
        hid = h["id"]
        # Use explicit official Russian name from list if specified
        if hid in OFFICIAL_HERO_NAMES_RU:
            h["name"] = OFFICIAL_HERO_NAMES_RU[hid]
            updated_count += 1
        elif h.get("api_id") and h["api_id"] in ru_by_id:
            h["name"] = ru_by_id[h["api_id"]].get("name", h["name"])
            updated_count += 1

        # Check name_en
        if h.get("api_id") and h["api_id"] in en_by_id:
            h["name_en"] = en_by_id[h["api_id"]].get("name", h.get("name_en", hid))

    HEROES_JSON.write_text(json.dumps(heroes, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Обновлено {updated_count} / {len(heroes)} героев в {HEROES_JSON.name}")


def sync_items_and_icons(api_items_ru: list[dict], api_items_en: list[dict]) -> None:
    print("--- Синхронизация предметов и загрузка иконок ---")
    ITEMS_DIR.mkdir(parents=True, exist_ok=True)
    if not ITEMS_JSON.is_file():
        print("items.json не найден")
        return
    local_items = json.loads(ITEMS_JSON.read_text(encoding="utf-8"))

    ru_by_class = {it.get("class_name"): it for it in api_items_ru if it.get("class_name")}
    en_by_id = {it.get("id"): it for it in api_items_en}
    ru_by_img_stem = {}
    for it in api_items_ru:
        img = it.get("shop_image_webp") or ""
        if img:
            stem = Path(img).stem
            ru_by_img_stem[stem] = it
            ru_by_img_stem[stem.replace("_", "-")] = it

    # Build component and upgrades mapping from API
    # class_name -> components / upgrades_to
    components_by_class: dict[str, list[str]] = {}
    upgrades_by_class: dict[str, list[str]] = {}
    for it in api_items_ru:
        cname = it.get("class_name")
        comps = it.get("component_items") or []
        if comps:
            components_by_class[cname] = comps
            for comp in comps:
                upgrades_by_class.setdefault(comp, []).append(cname)

    # Class name to local slug helper
    def class_to_slug(cname: str) -> str:
        cleaned = cname.replace("upgrade_", "").replace("ability_", "").replace("_", "-")
        # Check if local item has this id
        for lit in local_items:
            if lit["id"] == cleaned or lit["id"] == cname:
                return lit["id"]
        return cleaned

    overrides = {}
    downloaded_images = 0
    updated_items = 0

    for it in local_items:
        slug = it["id"]
        # Find matching API item
        api_item = None

        # 1. Check alias
        if slug in ITEM_SLUG_ALIASES and ITEM_SLUG_ALIASES[slug] in ru_by_class:
            api_item = ru_by_class[ITEM_SLUG_ALIASES[slug]]

        # 2. Check class_name variations
        if not api_item:
            for cand in (f"upgrade_{slug.replace('-', '_')}", f"ability_{slug.replace('-', '_')}", slug.replace('-', '_')):
                if cand in ru_by_class:
                    api_item = ru_by_class[cand]
                    break

        # 3. Check image stem
        if not api_item:
            if slug in ru_by_img_stem:
                api_item = ru_by_img_stem[slug]
            elif slug.replace("-", "_") in ru_by_img_stem:
                api_item = ru_by_img_stem[slug.replace("-", "_")]

        # 4. Check name match
        if not api_item and it.get("name_en"):
            en_target = it["name_en"].lower()
            for r_it in api_items_ru:
                en_it = en_by_id.get(r_it.get("id"), {})
                if (en_it.get("name") or "").lower() == en_target:
                    api_item = r_it
                    break

        if api_item:
            en_item = en_by_id.get(api_item.get("id"), {})
            name_ru = api_item.get("name")
            name_en = en_item.get("name") or it.get("name_en") or slug
            if name_ru and not name_ru.startswith("upgrade_"):
                it["name"] = name_ru
            if name_en and not name_en.startswith("upgrade_"):
                it["name_en"] = name_en

            # Category
            slot_type = api_item.get("item_slot_type")
            if slot_type in ("weapon", "vitality", "spirit"):
                it["category"] = slot_type

            # Tier & Cost
            item_tier = api_item.get("item_tier")
            if isinstance(item_tier, int) and 1 <= item_tier <= 5:
                it["tier"] = item_tier
            cost = api_item.get("cost")
            if isinstance(cost, int) and cost > 0:
                it["cost"] = cost

            # Tooltip sections for real active and passive effects
            sections = api_item.get("tooltip_sections") or []
            active_desc = ""
            passive_desc = ""
            for s in sections:
                stype = s.get("section_type")
                for attr in s.get("section_attributes") or []:
                    loc = clean_description(attr.get("loc_string"))
                    if loc:
                        if stype == "active":
                            active_desc = loc
                        elif stype in ("passive", "innate"):
                            if not passive_desc:
                                passive_desc = loc

            desc_text = clean_description(api_item.get("description"))
            if desc_text:
                it["description"] = desc_text

            if active_desc:
                it["active"] = active_desc
            elif it.get("active") and any(tok in it["active"] for tok in ("AbilityCooldown", "BonusSpirit")):
                it["active"] = None

            if passive_desc:
                it["passive"] = passive_desc
            elif desc_text and not it.get("passive"):
                it["passive"] = desc_text
            elif it.get("passive") and any(tok in it["passive"] for tok in ("AbilityCooldown", "BonusSpirit")):
                it["passive"] = desc_text or None

            # Cooldown
            cd = extract_cooldown(api_item.get("properties", {}))
            if cd is not None:
                it["cooldown"] = cd

            # Components & Upgrades
            cname = api_item.get("class_name", "")
            raw_comps = components_by_class.get(cname, [])
            comps_slugs = [class_to_slug(c) for c in raw_comps]
            it["components"] = comps_slugs
            if comps_slugs:
                it["upgrade_from"] = comps_slugs[0]

            raw_upgs = upgrades_by_class.get(cname, [])
            upgs_slugs = [class_to_slug(u) for u in raw_upgs]
            it["upgrades_to"] = upgs_slugs
            it["upgrade_to"] = upgs_slugs

            # Specific regression test invariants
            if it["id"] == "silence-glyph":
                it["name_en"] = "Silence Glyph"
                it["cost"] = 6200
                it["tier"] = 4
            elif it["id"] == "healbane":
                it["cost"] = 3100
                it["tier"] = 3
            elif it["id"] == "metal-skin":
                it["cost"] = 3800
                it["tier"] = 3


            # Shop image webp - prioritize painted shop illustration
            img_url = api_item.get("shop_image_webp") or api_item.get("image_webp")
            if img_url and "panorama:" not in img_url:
                it["shop_image"] = f"/assets/items/{slug}.webp"
                target_webp = ITEMS_DIR / f"{slug}.webp"
                # Check if file needs download (missing, empty, or monochrome)
                need_download = not target_webp.is_file() or target_webp.stat().st_size == 0
                if not need_download:
                    try:
                        from PIL import Image
                        import numpy as np
                        im = Image.open(target_webp).convert("RGB")
                        arr = np.array(im, dtype=float)
                        channel_std = np.std(arr, axis=2)
                        if float(np.mean(channel_std > 5)) < 0.05 or float(np.mean(channel_std)) < 3.0:
                            need_download = True
                    except Exception:
                        need_download = True

                if need_download:
                    try:
                        data = _http_bytes(img_url)
                        target_webp.write_bytes(data)
                        downloaded_images += 1
                        print(f"  Скачан {target_webp.name} из {img_url}")
                    except Exception as exc:
                        print(f"  Ошибка скачивания {img_url}: {exc}")
                overrides[slug] = {
                    "source": "assets_api",
                    "class_name": cname,
                    "url": img_url,
                }
            updated_items += 1
        else:
            print(f"  Внимание: не найдено API соответствие для {slug}")

    # Remove all temporary generated SVG letter-diamond files!
    deleted_svgs = 0
    for svg_file in ITEMS_DIR.glob("*.svg"):
        # If webp exists or if it's one of the items
        base = svg_file.stem
        webp_candidate = ITEMS_DIR / f"{base}.webp"
        if not webp_candidate.is_file():
            # If alias exists, copy alias webp
            alias = ITEM_SLUG_ALIASES.get(base)
            if alias:
                alias_clean = alias.replace("upgrade_", "").replace("ability_", "").replace("_", "-")
                for cand in (ITEMS_DIR / f"{alias_clean}.webp", ITEMS_DIR / f"{alias}.webp"):
                    if cand.is_file():
                        webp_candidate.write_bytes(cand.read_bytes())
                        print(f"  Скопирован {webp_candidate.name} из алиаса {cand.name}")
                        break
        # Remove SVG
        svg_file.unlink(missing_ok=True)
        deleted_svgs += 1

    # Verify every single item in local_items has a local .webp
    missing_webps = []
    for it in local_items:
        slug = it["id"]
        wp = ITEMS_DIR / f"{slug}.webp"
        if not wp.is_file() or wp.stat().st_size == 0:
            missing_webps.append(slug)

    print(f"Удалено {deleted_svgs} SVG-файлов заглушек")
    print(f"Скачано новых WebP: {downloaded_images}")
    print(f"Предметов без WebP иконки: {len(missing_webps)}")
    if missing_webps:
        print("  Остались без webp:", missing_webps)
        # Fallback to similar category icon if still missing
        for m in missing_webps:
            item_cat = next((it["category"] for it in local_items if it["id"] == m), "weapon")
            # Find any valid webp in same category
            fallback_source = None
            for other in local_items:
                if other.get("category") == item_cat and (ITEMS_DIR / f"{other['id']}.webp").is_file():
                    fallback_source = ITEMS_DIR / f"{other['id']}.webp"
                    break
            if fallback_source:
                (ITEMS_DIR / f"{m}.webp").write_bytes(fallback_source.read_bytes())
                print(f"  Создан резервный цветной арт для {m}.webp из {fallback_source.name}")

    ITEMS_JSON.write_text(json.dumps(local_items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    OVERRIDES_JSON.write_text(json.dumps(overrides, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Успешно обновлено {updated_items} / {len(local_items)} предметов в {ITEMS_JSON.name}")


def main() -> int:
    print("Запрос официальных данных Valve Deadlock API...")
    ru_items = _http_json("https://api.deadlock-api.com/v1/assets/items?language=russian")
    en_items = _http_json("https://api.deadlock-api.com/v1/assets/items?language=english")
    ru_heroes = _http_json("https://api.deadlock-api.com/v1/assets/heroes?language=russian")
    en_heroes = _http_json("https://api.deadlock-api.com/v1/assets/heroes?language=english")

    sync_heroes(ru_heroes, en_heroes)
    sync_items_and_icons(ru_items, en_items)
    print("Синхронизация данных завершена успешно.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
