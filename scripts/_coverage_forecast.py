"""Прогноз покрытия: сколько строк source_en.json останется без перевода после слияния кэша."""
import importlib.util
import json
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = Path(".").resolve()
spec = importlib.util.spec_from_file_location("tc", ROOT / "scripts" / "translate_counterpicks.py")
tc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tc)

source = json.loads(tc.SOURCE_JSON.read_text(encoding="utf-8"))
glossary = json.loads(tc.GLOSSARY_JSON.read_text(encoding="utf-8"))
tr = tc.Translator(glossary, 1, offline=True)

known: set[str] = set()
for scope in (source["heroes"], source["items"]):
    for chunk in scope.values():
        for path in tc.collect_paths(chunk):
            value = tc.get_at(chunk, path)
            if value.strip():
                known.add(value)

fixed = {s for s in known if tr._is_fixed(s)}
missing = sorted(s for s in known - fixed if s not in tr.cache)
print(f"уникальных строк в источнике: {len(known)}")
print(f"фиксированных меток/таймингов: {len(fixed)}")
print(f"в кэше сейчас: {len(tr.cache)}")
print(f"не хватает перевода: {len(missing)}")
for s in missing[:10]:
    print("  ?", s[:110])
