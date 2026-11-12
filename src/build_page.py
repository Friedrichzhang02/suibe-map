"""page_template.html + site/data.json → site/index.html"""
import json, os, re
ROOT = os.path.dirname(os.path.abspath(__file__)); SITE = f"{ROOT}/site"
t = open(f"{ROOT}/page_template.html", encoding="utf-8").read()
data = json.load(open(f"{SITE}/data.json")); meta = json.load(open(f"{SITE}/tiles/meta.json"))
rep = {"__LEAFLET_CSS__": open(f"{ROOT}/vendor/leaflet.css", encoding="utf-8").read(),
       "__LEAFLET_JS__": open(f"{ROOT}/vendor/leaflet.js", encoding="utf-8").read(),
       "__DATA__": json.dumps(data), "__MAXZ__": str(meta["maxz"]),
       "__PEOPLE__": str(data["people"]), "__EGRETS__": str(data["egrets"]),
       "__PXM__": str(data["w"] / float(re.search(r"CAM_WIDTH = ([0-9.]+)", open(f"{ROOT}/suibe.py", encoding="utf-8").read()).group(1))),
       "__SPRITES__": open(f"{ROOT}/sprites/sprites2.json").read(),
       "__BENCHES__": str(len(data["anim"].get("benches", [])))}
for a, b in rep.items(): t = t.replace(a, b)
left = sorted(set(re.findall(r"__[A-Z]+__", t)))
if left: print("未替换：", left)
open(f"{SITE}/index.html", "w", encoding="utf-8").write(t); print("ok")
