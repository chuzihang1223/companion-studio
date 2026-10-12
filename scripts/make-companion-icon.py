"""Reproducible, original vector-style application identity; no character assets."""
from pathlib import Path
from PIL import Image, ImageDraw

project = Path(__file__).resolve().parents[1]
assets = project / 'desktop/assets'
frontend = project / 'desktop/frontend/src/assets'
svg = '''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256">
<rect width="256" height="256" rx="60" fill="#162b3c"/>
<path d="M85 60h86a33 33 0 0 1 33 33v43a33 33 0 0 1-33 33h-57l-37 28 6-28a33 33 0 0 1-31-33V93a33 33 0 0 1 33-33Z" fill="#79dfd4"/>
<path d="M88 101h80M88 128h54" stroke="#163848" stroke-width="11" stroke-linecap="round"/>
<circle cx="189" cy="198" r="14" fill="#a9afff"/>
</svg>'''
assets.mkdir(exist_ok=True)
frontend.mkdir(exist_ok=True)
(assets / 'companion-studio.svg').write_text(svg, 'utf-8')
(frontend / 'companion-logo.svg').write_text(svg, 'utf-8')
scale = 4
image = Image.new('RGBA', (256*scale, 256*scale))
draw = ImageDraw.Draw(image)
box = lambda v: tuple(int(n*scale) for n in v)
draw.rounded_rectangle(box((0, 0, 256, 256)), radius=60*scale, fill='#162b3c')
draw.rounded_rectangle(box((52, 60, 204, 169)), radius=33*scale, fill='#79dfd4')
draw.polygon([(int(x*scale), int(y*scale)) for x,y in [(83,157),(77,197),(125,160)]], fill='#79dfd4')
for x2,y in [(168,101),(142,128)]:
    draw.line(box((88,y,x2,y)), fill='#163848', width=11*scale)
    for x in (88,x2): draw.ellipse(box((x-5.5,y-5.5,x+5.5,y+5.5)), fill='#163848')
draw.ellipse(box((175,184,203,212)), fill='#a9afff')
image = image.resize((512,512), Image.Resampling.LANCZOS)
image.save(assets / 'companion-studio.png')
image.save(assets / 'companion-studio.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
print('Created original Companion Studio SVG, PNG and Windows multi-size ICO')
