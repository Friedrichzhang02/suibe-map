"""渲染后处理：遮罩 → 点击轮廓；大图 → 瓦片金字塔"""
import json, math, os
import numpy as np, cv2
from PIL import Image

ROOT = os.path.dirname(os.path.abspath(__file__))
R = f"{ROOT}/renders"; OUT = f"{ROOT}/site"
W = 6000
os.makedirs(f"{OUT}/img", exist_ok=True)

mask = np.array(Image.open(f"{R}/mask_{W}.png").convert("RGB")).astype(int)
H = mask.shape[0]
shapes = {}
for key, ch in (("library", 0), ("lake", 1)):
    o = [c for c in range(3) if c != ch]
    m = ((mask[:, :, ch] > 128) & (mask[:, :, o[0]] < 100) & (mask[:, :, o[1]] < 100)).astype(np.uint8) * 255
    m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cs, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    c = max(cs, key=cv2.contourArea)
    ap = cv2.approxPolyDP(c, 3.5, True)[:, 0, :]
    shapes[key] = ap.tolist(); print(key, len(c), "→", len(ap))
meta = json.load(open(f"{R}/meta_{W}.json"))

# ---- 白鹭落点：湖岸向内 1.6 m 的浅水处，每 2 m 一个，只保留没被树或建筑挡住的
anim = meta["anim"]; P = anim["proj"]
vis = np.array(Image.open(f"{R}/vis_{W}.png").convert("L"))
def img(x, y, z): return (P[0][0] + x * P[1][0] + y * P[2][0] + z * P[3][0], P[0][1] + x * P[1][1] + y * P[2][1] + z * P[3][1])
def visible(x, y):
    for z in (0, 1.0, 2.2):
        u, v = img(x, y, z)
        for du in (-6, 0, 6):
            for dv in (-6, 0, 6):
                uu, vv = int(u + du), int(v + dv)
                if not (0 <= uu < W and 0 <= vv < H) or vis[vv, uu] < 128: return False
    return True
lake = np.array(anim["lake"]); n = len(lake); samples = []
for i in range(n):
    a, b = lake[i], lake[(i + 1) % n]; L = np.linalg.norm(b - a); t = (b - a) / L; inward = np.array([-t[1], t[0]])
    for k in range(max(1, int(L / 2.0))):
        q = a + t * (k * 2.0) + inward * 1.6
        samples.append((q, t, inward))
spots = []
tseats = np.array([s[:2] for s in anim.get("terraceSeats", [])]) if anim.get("terraceSeats") else np.zeros((0, 2))
for q, t, nrm in samples:
    if len(tseats) and np.min(np.hypot(*(tseats - q).T)) < 5.0: continue      # 台阶处不落脚
    if visible(*q):
        spots.append({"p": [round(q[0], 2), round(q[1], 2)], "t": [round(t[0], 3), round(t[1], 3)], "n": [round(nrm[0], 3), round(nrm[1], 3)]})
for i, s in enumerate(spots):
    s["i"] = i
    nxt = spots[i + 1] if i + 1 < len(spots) else None
    s["gap"] = nxt is None or math.dist(s["p"], nxt["p"]) > 3.2
anim["spots"] = spots
print("egret spots:", len(spots), "of", len(samples))

# 精灵图转 WebP（带透明）
os.makedirs(f"{OUT}/sprites", exist_ok=True)
for nm in ("egrets2", "egrets2_refl", "students2"):     # 精细版动作帧
    if os.path.exists(f"{ROOT}/sprites/{nm}.png"):
        Image.open(f"{ROOT}/sprites/{nm}.png").save(f"{OUT}/sprites/{nm}.webp", "WEBP", quality=88, alpha_quality=85, method=6)
import shutil; shutil.copy(f"{ROOT}/egrets.js", f"{OUT}/egrets.js")
shutil.copy(f"{ROOT}/water.js", f"{OUT}/water.js")
if True:
    shutil.copy(f"{ROOT}/students.js", f"{OUT}/students.js")
Image.fromarray(vis).resize((W // 4, H // 4), Image.NEAREST).convert("L").save(f"{OUT}/sprites/vis.png", optimize=True)
json.dump({"w": W, "h": H, "shapes": shapes, "pois": meta["pois"], "trees": meta["trees"], "people": meta["people"],
           "egrets": meta["egrets"], "anim": anim}, open(f"{OUT}/data.json", "w"))

SKIP_TILES = bool(os.environ.get("SKIP_TILES"))      # 只更新数据（测试用）
src = Image.open(f"{R}/scene_{W}.png" if os.path.exists(f"{R}/scene_{W}.png") else f"{R}/with_agents/scene_{W}.png").convert("RGBA")
T = 512; maxz = math.ceil(math.log2(max(src.size) / T)); count = 0
for z in (range(maxz, -1, -1) if not SKIP_TILES else []):
    f = 2 ** (maxz - z)
    im = src if f == 1 else src.resize((math.ceil(src.width / f), math.ceil(src.height / f)), Image.LANCZOS)
    os.makedirs(f"{OUT}/tiles/{z}", exist_ok=True)
    for tx in range(math.ceil(im.width / T)):
        for ty in range(math.ceil(im.height / T)):
            tile = Image.new("RGBA", (T, T), (0, 0, 0, 0))
            tile.paste(im.crop((tx * T, ty * T, min((tx + 1) * T, im.width), min((ty + 1) * T, im.height))), (0, 0))
            tile.save(f"{OUT}/tiles/{z}/{tx}_{ty}.webp", "WEBP", quality=84, method=5, alpha_quality=60); count += 1
if not SKIP_TILES: json.dump({"maxz": maxz, "tiles": count, "tile": T}, open(f"{OUT}/tiles/meta.json", "w"))
print("tiles", count, "maxz", maxz)

# 详情面板用的图书馆局部图
lib = np.array(shapes["library"]); x0, y0 = lib.min(0); x1, y1 = lib.max(0); pad = 160
crop = src.crop((x0 - pad, y0 - pad, x1 + pad, y1 + pad)).convert("RGB"); crop.thumbnail((1200, 1200))
crop.save(f"{OUT}/img/library.webp", "WEBP", quality=86)
lk = np.array(shapes["lake"]); x0, y0 = lk.min(0); x1, y1 = lk.max(0)
crop = src.crop((x0, y0, x1, y1)).convert("RGB"); crop.thumbnail((1000, 1000)); crop.save(f"{OUT}/img/lake.webp", "WEBP", quality=84)
src.convert("RGB").resize((1600, 1200), Image.LANCZOS).save(f"{OUT}/img/overview.webp", "WEBP", quality=86)
