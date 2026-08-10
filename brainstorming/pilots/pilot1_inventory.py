"""Pilot 1 — MI-CDM ECG attribute inventory.

Streams image_feature + measurement_ADD for the first N image_occurrences and
answers: which DICOM attributes did the ETL actually populate, which are
per-channel (value_order) vs per-study, and are they numeric or coded?
"""
import csv, sys, subprocess, collections, json

N_OCC = 300
FIRST_OCC = 100000001
LAST_OCC = FIRST_OCC + N_OCC - 1

concept = {}
with open('concept_ADD.csv') as fh:
    for r in csv.DictReader(fh):
        concept[r['concept_id']] = (r['concept_name'], r['concept_code'])

def stream(key):
    p = subprocess.Popen(
        ['aws', 's3', 'cp', f's3://dryou-workspace/Datasets/MIMIC-IV_CDM/Extension/{key}', '-'],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1 << 20)
    return p

# ---- pass 1: image_feature rows for our occurrence window
feats = []
p = stream('image_feature.csv')
rdr = csv.DictReader(p.stdout)
for row in rdr:
    occ = int(row['image_occurrence_id'])
    if occ > LAST_OCC:
        break
    feats.append(row)
p.kill()
print(f'image_feature rows for occ {FIRST_OCC}..{LAST_OCC}: {len(feats)}', flush=True)

want_mid = {int(f['image_feature_event_id']) for f in feats if f['image_feature_event_id']}
max_mid = max(want_mid)

# ---- pass 2: matching measurement rows
meas = {}
p = stream('measurement_ADD.csv')
rdr = csv.DictReader(p.stdout)
for row in rdr:
    mid = int(row['measurement_id'])
    if mid in want_mid:
        meas[mid] = row
    if mid > max_mid:
        break
p.kill()
print(f'measurement rows matched: {len(meas)}/{len(want_mid)}', flush=True)

# ---- inventory
inv = collections.defaultdict(lambda: {
    'n': 0, 'occ': set(), 'ordered': 0, 'unordered': 0,
    'numeric': 0, 'coded': 0, 'has_unit': 0, 'vals': collections.Counter(),
    'inst_uid': 0})
per_occ_counts = collections.Counter()

for f in feats:
    cid = f['image_feature_concept_id']
    name, code = concept.get(cid, (f'?{cid}', '?'))
    d = inv[(name, code)]
    d['n'] += 1
    d['occ'].add(f['image_occurrence_id'])
    if f['image_feature_value_order']:
        d['ordered'] += 1
    else:
        d['unordered'] += 1
    if f['image_instance_uid']:
        d['inst_uid'] += 1
    per_occ_counts[f['image_occurrence_id']] += 1
    m = meas.get(int(f['image_feature_event_id'])) if f['image_feature_event_id'] else None
    if m:
        if m['value_as_number']:
            d['numeric'] += 1
        if m['value_as_concept_id']:
            d['coded'] += 1
        if m['unit_concept_id']:
            d['has_unit'] += 1
        v = m['value_source_value']
        if len(d['vals']) < 400:
            d['vals'][v] += 1

n_occ = len(per_occ_counts)
print(f'\ndistinct image_occurrences: {n_occ}')
print(f'features per occurrence: min={min(per_occ_counts.values())} '
      f'max={max(per_occ_counts.values())} '
      f'mode={per_occ_counts.most_common()[len(per_occ_counts)//2][1]}')

rows = sorted(inv.items(), key=lambda kv: -kv[1]['n'])
print(f'\ndistinct DICOM attributes populated: {len(rows)}\n')
hdr = f'{"DICOM tag":<10} {"attribute":<44} {"n":>7} {"/occ":>5} {"ord":>5} {"num":>5} {"cod":>5} {"unit":>5}'
print(hdr)
print('-' * len(hdr))
for (name, code), d in rows:
    print(f'{code:<10} {name[:44]:<44} {d["n"]:>7} '
          f'{d["n"]/max(len(d["occ"]),1):>5.1f} '
          f'{d["ordered"]:>5} {d["numeric"]:>5} {d["coded"]:>5} {d["has_unit"]:>5}')

with open('pilot1_inventory.json', 'w') as fh:
    json.dump({f'{c}|{n}': {
        'n': d['n'], 'n_occ': len(d['occ']), 'ordered': d['ordered'],
        'unordered': d['unordered'], 'numeric': d['numeric'], 'coded': d['coded'],
        'has_unit': d['has_unit'], 'inst_uid': d['inst_uid'],
        'top_values': d['vals'].most_common(12),
    } for (n, c), d in rows}, fh, indent=1)
print('\nwrote pilot1_inventory.json')
