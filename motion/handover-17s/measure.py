import asyncio
import pathlib
from playwright.async_api import async_playwright

COMP = pathlib.Path(__file__).with_name('comp.html').resolve().as_uri()


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await (
            await b.new_context(viewport={'width': 1920, 'height': 1080})
        ).new_page()
        await pg.goto(COMP)
        await pg.evaluate('window.__ready')
        info = await pg.evaluate("""() => {
          const cv = document.createElement('canvas').getContext('2d');
          const out = {};
          for (const role of ['display','text','mono']) {
            cv.font = LOOK.font(role, 100);
            out[role+'_font'] = cv.font;
            out[role+'_w'] = cv.measureText('SOUTHSIDE MUSIC').width;
            out[role+'_wide'] = cv.measureText('MMMMMMMM').width;
          }
          out.stacks = LOOK.type;
          out.checkD = document.fonts.check("900 100px 'LookD'");
          out.checkCJK = document.fonts.check("400 40px 'LookCJK'");
          return out;
        }""")
        for k, v in info.items():
            print(k, '=', v)
        await b.close()


asyncio.run(main())
