"""Pilot 2b — corrected fidelity check.

Fixes over pilot2: SamplingFrequency / FilterLowFrequency / FilterHighFrequency
live on the multiplex group (WaveformSequence[0]), not on ChannelDefinitionSequence
items. Channel labels are compared by ORDER, since MI-CDM stores the standardized
concept ('I') where the file stores a CodeMeaning ('Lead I').
"""
import csv, subprocess, collections, json, os
import pydicom

FIRST_OCC, N_OCC = 100000001, 300
LAST_OCC = FIRST_OCC + N_OCC - 1
CACHE = 'pilot2_cache.json'
S3 = 's3://dryou-workspace/Datasets/MIMIC-IV_CDM/Extension'


def stream_rows(key, stop):
    p = subprocess.Popen(['aws', 's3', 'cp', f'{S3}/{key}', '-'],
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                         text=True, bufsize=1 << 20)
    for row in csv.DictReader(p.stdout):
        if stop(row):
            break
        yield row
    p.kill()


if os.path.exists(CACHE):
    blob = json.load(open(CACHE))
    cdm = {int(k): v for k, v in blob['cdm'].items()}
    occ_path = {int(k): v for k, v in blob['occ_path'].items()}
    print(f'loaded cache: {len(cdm)} occurrences')
else:
    concept = {r['concept_id']: r['concept_code']
               for r in csv.DictReader(open('concept_ADD.csv'))}
    occ_path = {int(r['image_occurrence_id']): r['local_path']
                for r in stream_rows('image_occurrence.csv',
                                     lambda r: int(r['image_occurrence_id']) > LAST_OCC)}
    feats = list(stream_rows('image_feature.csv',
                             lambda r: int(r['image_occurrence_id']) > LAST_OCC))
    want = {int(f['image_feature_event_id']) for f in feats}
    mx = max(want)
    meas = {}
    for r in stream_rows('measurement_ADD.csv', lambda r: int(r['measurement_id']) > mx):
        if int(r['measurement_id']) in want:
            meas[int(r['measurement_id'])] = r['value_source_value']
    cdm = collections.defaultdict(lambda: {'study': {}, 'chan': {}})
    for f in feats:
        occ = int(f['image_occurrence_id'])
        code = concept.get(f['image_feature_concept_id'], '?')
        val = meas[int(f['image_feature_event_id'])]
        if f['image_feature_value_order']:
            cdm[occ]['chan'].setdefault(f['image_feature_value_order'], {})[code] = val
        else:
            cdm[occ]['study'][code] = val
    cdm = dict(cdm)
    json.dump({'cdm': cdm, 'occ_path': occ_path}, open(CACHE, 'w'))
    print(f'built cache: {len(cdm)} occurrences')

# ---------------- verification ----------------
GROUP = {'003A001A': 'SamplingFrequency', '003A0005': 'NumberOfWaveformChannels',
         '003A0010': 'NumberOfWaveformSamples', '003A021A': 'WaveformBitsStored',
         '54001004': 'WaveformBitsAllocated'}
CHAN = {'003A0210': 'ChannelSensitivity', '003A0213': 'ChannelBaseline',
        '003A0220': 'FilterLowFrequency', '003A0221': 'FilterHighFrequency'}

agree = collections.Counter()
mismatch = collections.defaultdict(list)
order_ok = order_bad = 0
sf_per_channel_consistent = 0
label_seq = collections.Counter()
dropped = collections.Counter()
checked = 0

LEAD = {'Lead I': 'I', 'Lead II': 'II', 'Lead III': 'III',
        'aVR, augmented voltage, right': 'aVR',
        'aVL, augmented voltage, left': 'aVL',
        'aVF, augmented voltage, foot': 'aVF',
        **{f'Lead V{i}': f'V{i}' for i in range(1, 7)}}

for occ in sorted(cdm)[:120]:
    path = occ_path.get(occ)
    if not path or not os.path.exists(path):
        continue
    try:
        ds = pydicom.dcmread(path, force=True)
        mg = ds.WaveformSequence[0]
        chdefs = list(mg.ChannelDefinitionSequence)
    except Exception as e:
        mismatch['UNREADABLE'].append((occ, str(e)[:70]))
        continue
    checked += 1
    st = cdm[occ]['study']
    ch = {int(k): v for k, v in cdm[occ]['chan'].items()}

    # group-level scalars — SamplingFrequency is stored per-channel in MI-CDM
    for code, attr in GROUP.items():
        fv = getattr(mg, attr, None)
        if fv is None:
            mismatch[attr + ':NOT_IN_FILE'].append(occ); continue
        if code == '003A001A':
            vals = {c.get(code) for c in ch.values()}
            if len(vals) == 1:
                sf_per_channel_consistent += 1
            cv = vals.pop() if len(vals) == 1 else None
        else:
            cv = st.get(code)
        if cv is None:
            mismatch[attr + ':NOT_IN_CDM'].append(occ)
        elif abs(float(cv) - float(fv)) < 1e-12:
            agree[attr] += 1
        else:
            mismatch[attr].append((occ, cv, float(fv)))

    # per-channel scalars; filters are per-channel in file, study-level in MI-CDM
    for i, cd in enumerate(chdefs, start=1):
        for code, attr in CHAN.items():
            fv = getattr(cd, attr, None)
            cv = ch.get(i, {}).get(code, st.get(code))
            if fv is None:
                mismatch[attr + ':NOT_IN_FILE'].append((occ, i)); continue
            if cv is None:
                mismatch[attr + ':NOT_IN_CDM'].append((occ, i))
            elif abs(float(cv) - float(fv)) < 1e-12:
                agree[attr] += 1
            else:
                mismatch[attr].append((occ, i, cv, float(fv)))

    # channel ORDER via image_feature_value_order
    file_lbl = [LEAD.get(str(cd.ChannelSourceSequence[0].CodeMeaning),
                         str(cd.ChannelSourceSequence[0].CodeMeaning)) for cd in chdefs]
    cdm_lbl = [ch[i].get('003A0203') for i in sorted(ch)]
    label_seq['|'.join(file_lbl)] += 1
    if file_lbl == cdm_lbl:
        order_ok += 1
    else:
        order_bad += 1
        if order_bad <= 2:
            mismatch['CHANNEL_ORDER'].append((occ, cdm_lbl, file_lbl))

    for elem in list(chdefs[0]) + list(mg):
        tag = f'{elem.tag.group:04X}{elem.tag.element:04X}'
        if elem.VR == 'OW':
            continue
        if tag not in st and tag not in ch.get(1, {}):
            dropped[f'{tag}  {elem.name}'] += 1

print(f'\nDICOM files read: {checked}')
print(f'\n=== MI-CDM value == DICOM file value ===')
for k, v in sorted(agree.items(), key=lambda x: -x[1]):
    print(f'  {k:<26} {v:>6} matched')
print(f'\n=== mismatches / gaps ===')
print('  none' if not mismatch else '')
for k, v in mismatch.items():
    print(f'  {k:<30} n={len(v)}  e.g. {str(v[:1])[:110]}')
print(f'\n=== channel order recovered from image_feature_value_order ===')
print(f'  exact: {order_ok}   mismatch: {order_bad}')
print(f'  SamplingFrequency identical across all 12 channels: {sf_per_channel_consistent}/{checked}')
print(f'  distinct orderings in files: {len(label_seq)}')
for o, n in label_seq.most_common(4):
    print(f'    {n:>4}x  {o}')
print(f'\n=== in DICOM waveform group, absent from MI-CDM ===')
for k, v in dropped.most_common(20):
    print(f'  {v:>4}x  {k}')
