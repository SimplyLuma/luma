# SPDX-License-Identifier: Apache-2.0
"""Read-only PDF text geometry for native selection over the rendered paper."""
from dataclasses import dataclass


@dataclass(frozen=True)
class TextRegion:
    text: str
    x: float
    y: float
    size: float


def text_regions(text, rectangles, attributes=()):
    """Use the PDF's own line breaks and character positions, including columns."""
    result=[];offset=0
    for line in text.splitlines(keepends=True):
        value=line.rstrip('\r\n')
        boxes=rectangles[offset:offset+len(value)]
        visible=[box for char,box in zip(value,boxes) if not char.isspace() and box.x2>box.x1 and box.y2>box.y1]
        if value.strip() and visible:
            size=next((a.font_size for a in attributes if a.start_index<=offset<=a.end_index),visible[0].y2-visible[0].y1)
            result.append(TextRegion(value,min(box.x1 for box in visible),min(box.y1 for box in visible),size))
        offset+=len(line)
    return result


def document_text(document):
    regions=[]
    for number in range(document.get_n_pages()):
        page=document.get_page(number)
        if not page.get_text():regions.append([]);continue
        found,boxes=page.get_text_layout()
        regions.append(text_regions(page.get_text(),boxes,page.get_text_attributes()) if found else [])
    return regions
