"""每个时刻一套瓦片：site/tiles/t{HHMM}/{z}/{x}_{y}.webp"""
import math, os, sys, glob
from PIL import Image
ROOT = os.path.dirname(os.path.abspath(__file__)); T = 1024
def cut(path):
    key = os.path.basename(path)[4:8]; out = f"{ROOT}/site/tiles/t{key}"
    src = Image.open(path).convert("RGB"); maxz = math.ceil(math.log2(max(src.size) / T)); n = 0
    for z in range(maxz, -1, -1):
        f = 2 ** (maxz - z); im = src if f == 1 else src.resize((math.ceil(src.width / f), math.ceil(src.height / f)), Image.LANCZOS)
        os.makedirs(f"{out}/{z}", exist_ok=True)
        for tx in range(math.ceil(im.width / T)):
            for ty in range(math.ceil(im.height / T)):
                tile = Image.new("RGB", (T, T), (0, 0, 0)); tile.paste(im.crop((tx * T, ty * T, min((tx + 1) * T, im.width), min((ty + 1) * T, im.height))))
                tile.save(f"{out}/{z}/{tx}_{ty}.webp", "WEBP", quality=82, method=5); n += 1
    print(key, n, flush=True)
if __name__ == "__main__":
    for p in sys.argv[1:]: cut(p)
