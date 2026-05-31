from PIL import Image
from pathlib import Path

ICON_SIZES = [256, 128, 64, 48, 32, 16]
SRC = Path(__file__).resolve().parent.parent / 'icon.png'
DST = Path(__file__).resolve().parent.parent / 'icons' / 'app.ico'


def main() -> None:
    img = Image.open(SRC)
    img = img.convert('RGBA')
    DST.parent.mkdir(parents=True, exist_ok=True)
    img.save(DST, format='ICO', sizes=[(s, s) for s in ICON_SIZES])
    print(f'Icon saved: {DST}')


if __name__ == '__main__':
    main()
