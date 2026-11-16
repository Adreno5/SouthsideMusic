import asyncio
import json
import pathlib
from playwright.async_api import async_playwright

HERE = pathlib.Path(__file__).parent


async def main():
    async with async_playwright() as p:
        b = await p.chromium.launch()
        pg = await (
            await b.new_context(viewport={'width': 1920, 'height': 1080})
        ).new_page()
        await pg.goto((HERE / 'comp.html').resolve().as_uri())
        await pg.evaluate('window.__ready')
        score = await pg.evaluate('window.__score()')
        (HERE / 'score.json').write_text(json.dumps(score, indent=1), encoding='utf-8')
        print('dur', score['dur'], 'hits', len(score['hit']))
        print([(round(e['t'], 2), e['kind']) for e in score['hit'][:6]])
        await b.close()


asyncio.run(main())
