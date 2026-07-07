#!/usr/bin/env python3
"""
migrate_concept_ids.py
Adds concept_id + language to existing Greek vocab cards. Non-breaking.
Run once: python3 migrate_concept_ids.py
"""
import json, uuid
from pathlib import Path

USER_CARDS = Path(__file__).parent.parent / 'greek' / 'user_cards.json'

def cid():
    return 'c_' + uuid.uuid4().hex[:10]

with open(USER_CARDS, encoding='utf-8') as f:
    data = json.load(f)

added = 0
for card in data['cards']:
    if 'concept_id' not in card:
        card['concept_id'] = cid()
        added += 1
    if 'language' not in card:
        card['language'] = 'el'

with open(USER_CARDS, 'w', encoding='utf-8') as f:
    json.dump(data, f, ensure_ascii=False, indent=2)

print(f'✓ {len(data["cards"])} cards — concept_id added to {added}')
