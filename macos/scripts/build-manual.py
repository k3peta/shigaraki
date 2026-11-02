#!/usr/bin/env python3
"""Render the approved manual content without changing its wording."""
import base64
import argparse
import html
import json
import re
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image as PILImage
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph, Image, Spacer

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
parser = argparse.ArgumentParser()
parser.add_argument('--content', default='manual-content.json')
parser.add_argument('--html-only', action='store_true')
parser.add_argument('--output-dir', type=Path, default=ROOT / 'build' / 'manual')
parser.add_argument('--font-dir', type=Path, default=DOCS / 'fonts')
args = parser.parse_args()
OUT = args.output_dir.resolve()
OUT.mkdir(parents=True, exist_ok=True)
content = json.loads((DOCS / args.content).read_text())
basename = content.get('basename', '信楽_操作マニュアル')
pdfmetrics.registerFont(TTFont('FlowJapanese', str(args.font_dir / 'NotoSansJP-Regular.ttf')))
pdfmetrics.registerFont(TTFont('AppleSymbols', '/System/Library/Fonts/Apple Symbols.ttf'))

def rich(s):
    return escape(s).replace('⌘', '<font name="AppleSymbols">⌘</font>')

def html_text(s):
    return re.sub(r'https://[^\s<]+', lambda m: '<a href="' + m[0] + '">' + m[0] + '</a>', html.escape(s))

styles = {
    'kicker': ParagraphStyle('kicker', fontName='FlowJapanese', fontSize=9, leading=13, textColor=colors.HexColor('#16738b'), spaceAfter=7, wordWrap='CJK'),
    'title': ParagraphStyle('title', fontName='FlowJapanese', fontSize=22, leading=30, textColor=colors.HexColor('#142d3b'), spaceAfter=8, wordWrap='CJK'),
    'intro': ParagraphStyle('intro', fontName='FlowJapanese', fontSize=10.5, leading=16, textColor=colors.HexColor('#405364'), spaceAfter=12, wordWrap='CJK'),
    'heading': ParagraphStyle('heading', fontName='FlowJapanese', fontSize=12, leading=17, textColor=colors.HexColor('#16738b'), spaceBefore=10, spaceAfter=4, wordWrap='CJK'),
    'body': ParagraphStyle('body', fontName='FlowJapanese', fontSize=11, leading=16.5, textColor=colors.HexColor('#263b49'), spaceAfter=5, wordWrap='CJK'),
    'caption': ParagraphStyle('caption', fontName='FlowJapanese', fontSize=8.5, leading=12, textColor=colors.HexColor('#61727c'), spaceBefore=5, spaceAfter=4, wordWrap='CJK'),
}

md = ['# ' + content['title'], '', content['version'], '']
cards = []
for i, page in enumerate(content['pages'], 1):
    md.extend(['## ' + page['title'], '', page['intro'], ''])
    parts = [f'<p class="kicker">{html.escape(page["kicker"])}</p>', f'<h2>{html.escape(page["title"])}</h2>', f'<p class="intro">{html.escape(page["intro"])}</p>']
    if page.get('image'):
        path = DOCS / 'screenshots' / page['image']
        encoded = base64.b64encode(path.read_bytes()).decode()
        mime = 'image/jpeg' if PILImage.open(path).format == 'JPEG' else 'image/png'
        figure = f'<figure><img src="data:{mime};base64,{encoded}" alt="{html.escape(page["caption"], quote=True)}"><figcaption>{html.escape(page["caption"])}</figcaption></figure>'
        image_md = [f'![{page["caption"]}](screenshots/{page["image"]})', '', page['caption'], '']
        if not page.get('image_after_blocks'):
            parts.append(figure)
            md.extend(image_md)
    for block in page['blocks']:
        parts.append(f'<h3>{html.escape(block["heading"])}</h3>')
        md.extend(['### ' + block['heading'], ''])
        if 'steps' in block:
            parts.append('<ol>' + ''.join(f'<li>{html.escape(s)}</li>' for s in block['steps']) + '</ol>')
            md.extend([f'{n}. {s}' for n, s in enumerate(block['steps'], 1)] + [''])
        for s in block.get('paragraphs', []):
            parts.append('<p>' + html_text(s) + '</p>')
            md.extend([s, ''])
    if page.get('image') and page.get('image_after_blocks'):
        parts.append(figure)
        md.extend(image_md)
    cards.append(f'<section id="p{i}">' + ''.join(parts) + '</section>')

(DOCS / (basename + '.md')).write_text('\n'.join(md))
nav = ''.join(f'<a href="#p{i}">{html.escape(p["kicker"])} · {html.escape(p["title"])}</a>' for i, p in enumerate(content['pages'], 1))
stylesheet = '''
:root{color-scheme:light;font-family:-apple-system,BlinkMacSystemFont,"Hiragino Kaku Gothic ProN",sans-serif;color:#243d4c;background:#edf3f6}
body{margin:0}header,main{max-width:880px;margin:0 auto;padding:30px 24px}header{padding-bottom:0}h1{font-size:28px;color:#153a4e}header p{color:#617784}nav{display:flex;gap:8px;flex-wrap:wrap;margin:20px 0}nav a{font-size:12px;background:white;padding:10px;border-radius:10px;color:#24677e;text-decoration:none}
section{background:white;border:1px solid #dce8ee;border-radius:20px;padding:32px;margin-bottom:24px;box-shadow:0 8px 24px #18384e06}h2{font-size:26px;margin:6px 0 14px;color:#16374a}h3{font-size:17px;margin:24px 0 8px;color:#16738b}p,li{font-size:15px;line-height:1.85}p{margin:8px 0}li{margin:8px 0}.intro{color:#4e6775}.kicker{font-size:12px;letter-spacing:.08em;color:#197894}figure{margin:22px 0}figure img{display:block;width:auto;max-width:100%;max-height:650px;margin:auto;border-radius:12px}figcaption{font-size:12px;line-height:1.65;color:#667b88;margin-top:12px}
@media(max-width:600px){header,main{padding:18px 12px}section{padding:22px 18px}h2{font-size:22px}}
@media print{body{background:white}header,main{max-width:none;padding:0}nav{display:none}section{box-shadow:none;border:0;border-radius:0;break-before:page;padding:0}figure img{max-height:110mm}p,li{font-size:10pt;line-height:1.5}h2{font-size:20pt}h3{font-size:12pt}header{margin-bottom:10mm}}
'''
document = '<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>' + html.escape(content['title']) + '</title><style>' + stylesheet + '</style><header><h1>' + html.escape(content['title']) + '</h1><p>' + html.escape(content['version']) + '</p><nav>' + nav + '</nav></header><main>' + ''.join(cards) + '</main></html>'
(DOCS / (basename + '.html')).write_text(document)
if args.html_only:
    raise SystemExit(0)

W, H = A4
M = 40
usable = W - 2 * M
pdf_path = OUT / (basename + '.pdf')
c = canvas.Canvas(str(pdf_path), pagesize=A4)
c.setTitle(content['title'])
c.setAuthor('信楽')

def paragraph(s, kind):
    return Paragraph(rich(s), styles[kind])

def height(items):
    return sum(f.wrap(usable, H)[1] + f.getSpaceBefore() + f.getSpaceAfter() for f in items)

metrics = []
for n, page in enumerate(content['pages'], 1):
    intro = [paragraph(page['kicker'], 'kicker'), paragraph(page['title'], 'title'), paragraph(page['intro'], 'intro')]
    tail = []
    if page.get('caption'):
        tail.append(paragraph(page['caption'], 'caption'))
    for block in page['blocks']:
        tail.append(paragraph(block['heading'], 'heading'))
        for i, s in enumerate(block.get('steps', []), 1):
            tail.append(paragraph(f'{i}. {s}', 'body'))
        for s in block.get('paragraphs', []):
            tail.append(paragraph(s, 'body'))
    items = intro[:]
    image_width = None
    if page.get('image'):
        path = DOCS / 'screenshots' / page['image']
        iw, ih = PILImage.open(path).size
        preferred = 430 if n == 1 else 375
        max_height = H - 2*M - 20 - height(intro + tail)
        image_width = min(preferred, max_height * iw / ih)
        if image_width < 290:
            raise ValueError(f'Page {n}: image would be too small: {image_width}')
        picture = Image(str(path), width=image_width, height=image_width*ih/iw)
        if not page.get('image_after_blocks'):
            items.append(picture)
    items.extend(tail)
    if page.get('image') and page.get('image_after_blocks'):
        items.append(picture)
    y = H - M
    for f in items:
        fw, fh = f.wrap(usable, H)
        y -= f.getSpaceBefore()
        if y - fh < M + 20:
            raise ValueError(f'Page {n}: overflow at {y-fh}')
        x = (W - fw)/2 if isinstance(f, Image) else M
        f.drawOn(c, x, y - fh)
        y -= fh + f.getSpaceAfter()
    c.setStrokeColor(colors.HexColor('#d9e6eb'))
    c.line(M, 32, W-M, 32)
    c.setFont('FlowJapanese', 8)
    c.setFillColor(colors.HexColor('#647985'))
    c.drawString(M, 20, content['title'] + ' / ' + content['version'])
    c.drawRightString(W-M, 20, str(n) + ' / ' + str(len(content['pages'])))
    c.showPage()
    metrics.append({'page':n, 'image_width_pt':image_width, 'bottom_pt':y})
c.save()
(OUT / (basename + '-layout.json')).write_text(json.dumps(metrics, indent=2))
print(json.dumps({'pdf':str(pdf_path), 'pages':len(content['pages']), 'metrics':metrics}, ensure_ascii=False, indent=2))
