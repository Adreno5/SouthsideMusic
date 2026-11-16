import asyncio
import sys
import pathlib
from playwright.async_api import async_playwright

HERE = pathlib.Path(__file__).parent
COMP = (HERE / 'comp.html').resolve().as_uri()


async def main(times, outdir):
    out = HERE / outdir
    out.mkdir(exist_ok=True)
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await (
            await b.new_context(viewport={'width': 1920, 'height': 1080})
        ).new_page()
        errs = []
        pg.on('pageerror', lambda e: errs.append(str(e)))
        await pg.goto(COMP)
        await pg.evaluate('window.__ready')
        for t in times:
            await pg.evaluate(f'window.__seek({t})')
            await pg.screenshot(path=str(out / f't{t:05.2f}.png'))
        print('errors:', errs or 'none')
        await b.close()


if __name__ == '__main__':
    times = [float(x) for x in sys.argv[1].split(',')]
    asyncio.run(main(times, sys.argv[2] if len(sys.argv) > 2 else 'shots'))
