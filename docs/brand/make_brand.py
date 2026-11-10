import asyncio, math
from playwright.async_api import async_playwright

def arc(cx, cy, r, a0, a1):
    # angles in clock hours (0 = 12 o'clock)
    p = lambda h: (cx + r*math.sin(h/12*2*math.pi), cy - r*math.cos(h/12*2*math.pi))
    x0,y0 = p(a0); x1,y1 = p(a1)
    large = 1 if (a1-a0) % 12 > 6 else 0
    return f"M{cx} {cy} L{x0:.2f} {y0:.2f} A{r} {r} 0 {large} 1 {x1:.2f} {y1:.2f} Z"

def icon_svg(dark=False):
    ring = "#e8eef5" if dark else "#1f3b57"
    face = "#1f3b57" if dark else "#ffffff"
    tick = "#9fb3c8" if dark else "#7b8ea3"
    green = "#3ecf6e"
    bolt = "#ffb300"
    ticks = ""
    for h in range(12):
        a = h/12*2*math.pi
        r0, r1 = (72, 84) if h % 3 == 0 else (77, 84)
        ticks += f'<line x1="{128+r0*math.sin(a):.2f}" y1="{128-r0*math.cos(a):.2f}" x2="{128+r1*math.sin(a):.2f}" y2="{128-r1*math.cos(a):.2f}" stroke="{tick}" stroke-width="{6 if h%3==0 else 4}" stroke-linecap="round"/>'
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="16 16 224 224" width="256" height="256">
  <circle cx="128" cy="128" r="104" fill="{face}"/>
  <path d="{arc(128,128,96,1.5,4.5)}" fill="{green}" opacity="0.9"/>
  <circle cx="128" cy="128" r="104" fill="none" stroke="{ring}" stroke-width="16"/>
  {ticks}
  <path d="M141 44 L84 140 L121 140 L109 212 L172 108 L134 108 Z" fill="{bolt}" stroke="{face}" stroke-width="7" stroke-linejoin="round"/>
</svg>'''

def logo_svg(dark=False):
    text = "#e8eef5" if dark else "#1f3b57"
    sub = "#9fb3c8" if dark else "#5d7189"
    inner = icon_svg(dark).split(">",1)[1].rsplit("</svg>",1)[0]
    return f'''<svg xmlns="http://www.w3.org/2000/svg" viewBox="16 16 884 224" width="1010" height="256">
  <g>{inner}</g>
  <text x="282" y="122" font-family="Inter, Roboto, 'Segoe UI', Arial, sans-serif" font-weight="700" font-size="84" fill="{text}">Cheapest Hour</text>
  <text x="285" y="196" font-family="Inter, Roboto, 'Segoe UI', Arial, sans-serif" font-weight="500" font-size="62" fill="{sub}">Scheduler</text>
</svg>'''

async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch(args=["--no-sandbox"])
        jobs = []
        for dark in (False, True):
            pre = "dark_" if dark else ""
            for scale, suf in ((1, ""), (2, "@2x")):
                jobs.append((icon_svg(dark), 256, 256, scale, f"{pre}icon{suf}.png"))
                jobs.append((logo_svg(dark), 1010, 256, scale, f"{pre}logo{suf}.png"))
        for svg, w, h, scale, name in jobs:
            ctx = await b.new_context(viewport={"width": w, "height": h}, device_scale_factor=scale)
            page = await ctx.new_page()
            await page.set_content(f"<html><body style='margin:0;background:transparent'>{svg}</body></html>")
            await page.locator("svg").screenshot(path=name, omit_background=True)
            await ctx.close()
        await b.close()
    open("icon.svg","w").write(icon_svg()); open("dark_icon.svg","w").write(icon_svg(True))
    # preview on light and dark background
    prev = f"""<html><body style='margin:0;display:flex'>
      <div style='background:#fafafa;padding:24px;display:flex;gap:24px;align-items:center'>
        <img src='icon@2x.png' width=128><img src='icon.png' width=48><img src='icon.png' width=24><img src='logo.png' height=96></div>
      <div style='background:#111;padding:24px;display:flex;gap:24px;align-items:center'>
        <img src='dark_icon@2x.png' width=128><img src='dark_icon.png' width=48><img src='dark_icon.png' width=24><img src='dark_logo.png' height=96></div></body></html>"""
    open("preview.html","w").write(prev)
asyncio.run(main())
