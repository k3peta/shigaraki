#!/usr/bin/env python3
"""Manually rebuild the local prefecture/municipality index from Japan Post UTF-8 data."""
import argparse
import csv
import datetime
import hashlib
import io
import json
from pathlib import Path
import urllib.request
import zipfile

parser = argparse.ArgumentParser()
parser.add_argument('--release-date', required=True, type=datetime.date.fromisoformat)
parser.add_argument('--zip', type=Path, help='Use a previously downloaded full UTF-8 archive')
args = parser.parse_args()
url = 'https://www.post.japanpost.jp/service/search/zipcode/download/utf/zip/utf_ken_all.zip'
if args.zip:
    data = args.zip.read_bytes()
else:
    with urllib.request.urlopen(url, timeout=30) as response:
        data = response.read(5_000_001)
if len(data) > 5_000_000:
    raise ValueError('Archive too large')
with zipfile.ZipFile(io.BytesIO(data)) as archive:
    files = [item for item in archive.infolist() if item.filename.lower().endswith('.csv')]
    if len(files) != 1 or files[0].file_size > 40_000_000:
        raise ValueError('Unexpected archive layout/size')
    rows = csv.reader(io.StringIO(archive.read(files[0]).decode('utf-8-sig')))
    places = {}
    prefectures = {}
    for row in rows:
        if len(row) != 15 or len(row[0]) != 5 or not row[0].isdigit():
            raise ValueError('Unexpected postal row')
        pref_id, pref, pref_kana, name, kana = row[0][:2], row[6], row[3], row[7], row[4]
        for field in (pref, pref_kana, name, kana):
            if not field or any(c in field for c in '\t\r\n'):
                raise ValueError('Invalid place text')
        record = (row[0], pref, name, kana)
        if row[0] in places and places[row[0]] != record:
            raise ValueError('Conflicting municipality code')
        places[row[0]] = record
        prefectures[pref_id] = (pref_id, pref, pref, pref_kana)
if set(prefectures) != {f'{i:02d}' for i in range(1, 48)} or not 1800 <= len(places) <= 2200:
    raise ValueError('Unexpected national coverage')
records = sorted(prefectures.values()) + sorted(places.values())
tsv = ''.join('\t'.join(row) + '\n' for row in records).encode()
root = Path(__file__).resolve().parents[1] / 'Sources/KoeFlowCore/Resources'
root.mkdir(parents=True, exist_ok=True)
(root / 'geography.tsv').write_bytes(tsv)
metadata = {'schemaVersion': 1, 'source': 'Japan Post postal address data (processed prefecture/municipality index)',
    'sourceURL': url, 'descriptionURL': 'https://www.post.japanpost.jp/service/search/zipcode/download/utf-readme.html',
    'releaseDate': str(args.release_date), 'retrievedAt': datetime.datetime.now(datetime.timezone.utc).isoformat(),
    'archiveSHA256': hashlib.sha256(data).hexdigest(), 'indexSHA256': hashlib.sha256(tsv).hexdigest(),
    'prefectures': len(prefectures), 'municipalities': len(places), 'entries': len(records),
    'scope': 'Prefecture and postal municipality names; no station names or town areas. No automatic replacement.'}
(root / 'geography-source.json').write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + '\n')
print(json.dumps({'entries': len(records), 'bytes': len(tsv), 'sourceRelease': str(args.release_date)}))
