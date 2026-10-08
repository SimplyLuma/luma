# SPDX-License-Identifier: Apache-2.0
"""Read-only OCR adapter: pixels enter stdin, recognized words leave stdout."""
from dataclasses import dataclass
import csv
import io
import re
import shutil
import subprocess


@dataclass(frozen=True)
class TextRegion:
    text: str
    x: int
    y: int
    width: int
    height: int


def recognize(png_bytes):
    program=shutil.which('tesseract')
    if program is None:return []
    result=subprocess.run([program,'stdin','stdout','tsv'],input=png_bytes,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=30,check=True)
    regions=[];lines={}
    for row in csv.DictReader(io.StringIO(result.stdout.decode('utf-8')),delimiter='\t'):
        text=(row.get('text') or '').strip()
        if not text:continue
        try:
            if float(row['conf'])<40:continue
            region=TextRegion(text,int(row['left']),int(row['top']),int(row['width']),int(row['height']))
        except (KeyError,ValueError):continue
        if region.width>0 and region.height>0:
            if row.get('line_num'):
                key=tuple(row.get(k,'') for k in ('page_num','block_num','par_num','line_num'))
                lines.setdefault(key,[]).append(region)
            else:regions.append(region)
    for words in lines.values():
        words.sort(key=lambda region:region.x)
        x=min(w.x for w in words);y=min(w.y for w in words)
        regions.append(TextRegion(' '.join(w.text for w in words),x,y,
            max(w.x+w.width for w in words)-x,max(w.y+w.height for w in words)-y))
    return regions


def detection_kind(text):
    """Identify complete OCR lines; never infer a contact or calendar record."""
    if re.fullmatch(r'[$€£]\s*\d[\d,.]*',text.strip()):return 'money'
    if re.search(r'\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\w*\s+\d{1,2}\b|\b\d{1,2}/\d{1,2}/\d{2,4}\b',text,re.I):return 'date'
    if re.fullmatch(r'[+()\d\s.-]+',text) and 7<=sum(c.isdigit() for c in text)<=15:return 'tel'
    if re.search(r'\b\d+\s+.*\b(?:avenue|ave|street|st|road|rd|boulevard|blvd|lane|ln)\b',text,re.I):return 'addr'
    return None
