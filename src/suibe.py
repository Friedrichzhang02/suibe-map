"""
上海对外经贸大学（松江校区）图书馆 · 环境场景程序化建模（Blender 4.2+ / 5.x）

数据来源：
  - 图书馆平面形状：图书馆官网「馆舍平面图」1F（L 形、圆角、拱门入口位置）
  - 位置、尺寸、朝向：卫星影像（Google，z18–z20）量取，约 81.5 m × 65.6 m
  - 思源湖轮廓：OpenStreetMap way 359022579
  - 层数：地上 5 层（图书馆官网「本馆概况」）
  - 层高、立面细节：按照片估算

用法：
  Blender 界面：Scripting → 打开本文件 → Run Script
  命令行：blender -b -P suibe.py -- <stage> [宽] [高] [采样]
          stage = scene | mask | vis | save | pois | preview
  环境变量 NO_AGENTS=1：不生成静态学生和白鹭；NO_EGRETS=1：只去掉白鹭（网页动画版用）
"""
import bpy, bmesh, math, random, sys, json, os
_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(_HERE, "pylibs")):      # 本地 Blender：trees2.py 与 shapely 放在同目录
    if os.path.isdir(_p) and _p not in sys.path: sys.path.insert(0, _p)
from mathutils import Vector, Matrix, Euler
import numpy as np

# ============================== 参数 ==============================
# 坐标系：原点 = 图书馆中心；x 向东，y 向北，单位米
M_PER_PX = 0.6549          # 卫星拼图每像素米数（z18，纬度 31.048°）
PX0 = (463, 650)           # 图书馆中心在拼图中的像素位置

# 图书馆 L 形平面（逆时针），按 1F 平面图 + 卫星图量取
LIB_FOOT = [(-40.5, -32.75), (40.5, -32.75), (40.5, 32.75), (7.6, 32.75), (7.6, -2.47), (-40.5, -2.47)]
LIB_RADII = [6.3, 6.3, 5.0, 5.0, 3.1, 6.3]
LIB_FLOORS, LIB_FLOOR_H = 5, 4.8
# 拱门：(立面上的参考点, 宽, 高)；北侧临湖、南侧主入口、东南转角
LIB_ARCHES = [((-3.0, -2.47), 9.5, 9.2), ((-1.5, -32.75), 9.5, 9.2), ((38.6, -30.9), 13.0, 8.6)]
LIB_ATRIUM = ((-3.0, -2.47), 15.0)     # 北立面（临湖）通高玻璃中庭：中心点、宽度

SCENE_X = (-220, 150)      # 场景范围（米）
SCENE_Y = (-100, 190)

SUN_SRC = (-0.85, -0.45, 0.62)   # 太阳所在方向（秋日午后，西南偏西）
if os.environ.get("HQ"): SUN_SRC = (-0.80, -0.55, 0.47)   # 升级版：下午 3~4 点，太阳更低、影子更长

# ---- 一天中的时刻（TIME=HH:MM）：按松江校区经纬度、春分/秋分日计算真实太阳位置
def sun_pos(hhmm, lat=31.05, lon=121.21, tz=8, doy=266):
    h, m = map(int, hhmm.split(":")); t = h + m / 60
    g = 2 * math.pi / 365 * (doy - 1 + (t - 12) / 24)
    eot = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g) - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    tst = t * 60 + eot + 4 * lon - 60 * tz; ha = math.radians(tst / 4 - 180); la = math.radians(lat)
    cz = math.sin(la) * math.sin(decl) + math.cos(la) * math.cos(decl) * math.cos(ha)
    el = 90 - math.degrees(math.acos(max(-1, min(1, cz))))
    az = (math.degrees(math.atan2(math.sin(ha), math.cos(ha) * math.sin(la) - math.tan(decl) * math.cos(la))) + 180) % 360
    return el, az
TIME = os.environ.get("TIME")
SUN_EL, SUN_AZ = sun_pos(TIME) if TIME else (28.0, 235.0)
if TIME:
    _e, _a = math.radians(max(SUN_EL, 2.0)), math.radians(SUN_AZ)
    SUN_SRC = (math.cos(_e) * math.sin(_a), math.cos(_e) * math.cos(_a), math.sin(_e))
# 夜间灯光：窗户亮灯比例、路灯是否点亮
_LIT = {"05:20": 0.12, "06:15": 0.04, "17:30": 0.07, "18:10": 0.55, "20:00": 0.72, "02:00": 0.14}
LIT_WIN = _LIT.get(TIME, 0.0 if SUN_EL > 5 else 0.5) if TIME else 0.0
LAMPS_ON = bool(TIME) and SUN_EL < 4
# 曝光随时刻变化；人工灯光的强度按曝光反向换算，保证不同时刻看起来亮度一致、不过曝
def _expo(el):
    if el > 40: return 0.05
    if el > 10: return 0.15 + (40 - el) / 30 * 0.15
    if el > 0: return 0.45 + (10 - el) * 0.13
    if el > -12: return 1.6 + (-el) * 0.12
    return 2.5
EXPO = _expo(SUN_EL) if TIME else 0.3
ART = 2 ** -EXPO                                   # 人工光源换算系数
HQ = bool(os.environ.get("HQ"))
DETAIL = os.environ.get("DETAIL", "1") != "0"
WATER = int(os.environ.get("WATER", "0"))      # 水面效果：0 现状 | 1 清澈渐变 | 2 清澈+风纹+焦散 | 3 风格化（平涂+岸边白线）   # 1 = 精细外立面（圆润线脚、窗洞倒角、竖向遮阳片、厚拱门套）                 # 1 = 升级版光影与材质（物理天空、下午斜阳、材质细节）
NO_AGENTS = bool(os.environ.get("NO_AGENTS"))   # 1 = 不生成静态学生和白鹭（改由网页动画呈现）
NO_EGRETS = bool(os.environ.get("NO_EGRETS"))   # 1 = 只去掉静态白鹭（白鹭由网页动画呈现）
CAM_TARGET = (-48, 40, 0)  # 画面中心（米）
CAM_WIDTH = 300            # 画面宽度覆盖的米数

TREE_SCALE = 0.72         # 全部树木的整体缩放（道路和建筑是主角，树不宜过大）
TREE_MIN_SCALE = 0.5      # 为避免遮挡而缩小时的下限；再小仍遮挡则去掉这棵树
OCCLUDE_MAX = 2.5         # 每棵树允许遮挡关键区域（路面、人行道、步道、建筑）的面积，m²
# 各树种（缩放 1 时）的树冠顶高、树冠半径，用于估算遮挡
TREE_DIMS = {"Camphor": (8.0, 4.2), "Camphor2": (8.0, 4.2), "Willow": (7.4, 3.2), "Sequoia": (13.5, 2.1),
             "Ginkgo": (8.6, 2.4), "Shrub": (1.4, 1.2)}
PEOPLE_SCALE = 1.3         # 人物放大倍数（地图上更易看清）
EGRET_SCALE = 2.3          # 白鹭放大倍数
SEED = 11
# 湖整体下沉：环湖步道比草地低 RING_Z，水面再低一些；东岸与南岸用三级台阶连接地面，其余湖岸为草坡
RING_Z = -0.9              # 环湖步道（以及下沉的粉色广场）高度
WATER_Z = -1.25            # 水面高度
RING_W = 3.5               # 环湖步道宽度（从水边量起）
STEP_D, STEP_N = 0.8, 3    # 台阶进深、级数（每级高 -RING_Z / 3 = 0.3 m）
# 用台阶（而不是草坡）连接的湖岸范围
STEP_ZONES = [[(-45, 48), (25, 48), (25, 240), (-45, 240)],          # 湖东岸：三级台阶，在 y=48~60 之间逐级收窄
              [(-130, -5), (-45, -5), (-45, 48), (-130, 48)],        # 湖南岸（长条铺装带 + 粉色广场）
              [(-32, -5), (-15, -5), (-15, 47), (-32, 47)]]          # 湖南岸靠图书馆：从图书馆这层下到沿湖步道的台阶
EAST_TAPER = (48.0, 60.0)  # 东岸台阶从 y=60（满宽）到 y=48（无）逐级收窄
# 湖东南角（靠近图书馆）：沿湖步道背后是垂直挡土墙，没有台阶
WALL_ZONE = [(-15, -5), (30, -5), (30, 48), (-15, 48)]
def _lib_arc(y0=4.6, x0=0.6, r=11.0, n=16):
    cx, cy = x0 - r, y0 + r
    arc = [(cx + r * math.cos(a), cy + r * math.sin(a)) for a in np.linspace(-math.pi / 2, 0, n)]
    return [(-50, 10), (-42, 6.0), (-32, y0)] + arc + [(x0, 24.0)]
LIB_ARC = _lib_arc()
PINK_SUNKEN = [(-76, 15), (-57, 15), (-57, 34), (-76, 34)]           # 粉色广场的下沉部分（北侧与环湖步道相连）
PINK_OUTER = [(-81, 7), (-51, 7), (-51, 30), (-81, 30)]              # 粉色广场外圈（地面高度）
LINEAR_PLAZA = [(-127, 13.5), (-78, 13.5), (-78, 26), (-127, 26)]    # 长条铺装带（地面高度，北缘接台阶）
# 按新卫星图（2025 前后）描绘的步道：(折线, 宽度)，单位米
PATHS = [
    ([(-127, 10), (-50, 10)], 3.0),                                               # 湖南岸东西向步道
    # 图书馆门前的弧形路：贴着南翼北立面向东，在 L 形内角弯成圆弧，再沿东翼西立面向北
    (LIB_ARC, 4.0),
    ([(-212, -10), (-127, -10)], 3.0),
    ([(-140, -36), (-73, -36)], 3.0),
    ([(-71, 11), (-71, -48)], 3.0),
    ([(-190, -10), (-190, -60)], 3.0),
    ([(-210, 25), (-210, -60)], 3.0),
    ([(-139.5, -10), (-139.5, -48)], 3.5),
    ([(-12, 87), (5, 88)], 2.2), ([(-14, 140), (0, 139)], 2.2),
]
# 铺装广场（多边形，米）
PLAZAS_M = [
    ("Plaza_PinkStrip", [(-142, -10), (-127, -10), (-127, 26), (-142, 26)], "pink"),
    ("Plaza_LibWest", [(-57, -42), (-43, -42), (-43, 4), (-57, 4)], "paving"),
]

COLORS = {
    "cream": (0.80, 0.74, 0.62), "band": (0.90, 0.86, 0.77), "glass": (0.12, 0.22, 0.32),
    "mullion": (0.70, 0.73, 0.76), "roof": (0.56, 0.56, 0.54), "roof2": (0.44, 0.44, 0.43),
    "grass": (0.30, 0.50, 0.12), "grass2": (0.46, 0.55, 0.16), "path": (0.80, 0.76, 0.66),
    "paving": (0.68, 0.66, 0.62), "asphalt": (0.24, 0.25, 0.27), "water": (0.08, 0.20, 0.22),
    "shore": (0.62, 0.60, 0.55), "trunk": (0.30, 0.20, 0.12),
    "camphor": (0.08, 0.26, 0.06), "camphor2": (0.13, 0.33, 0.07), "ginkgo": (0.85, 0.66, 0.08),
    "sequoia": (0.62, 0.26, 0.07), "willow": (0.40, 0.52, 0.12), "shrub": (0.14, 0.34, 0.10),
    "white": (0.95, 0.95, 0.93), "black": (0.03, 0.03, 0.03), "yellow": (0.95, 0.75, 0.10),
    "skin": (0.85, 0.66, 0.50), "hair": (0.05, 0.04, 0.03), "jeans": (0.12, 0.17, 0.30),
    "darkpants": (0.08, 0.08, 0.09), "khaki": (0.55, 0.48, 0.34),
    "nb_wall": (0.86, 0.85, 0.82), "nb_glass": (0.20, 0.26, 0.32), "nb_roof": (0.60, 0.60, 0.60),
    "car1": (0.85, 0.85, 0.85), "car2": (0.10, 0.10, 0.12), "car3": (0.55, 0.08, 0.06), "car4": (0.30, 0.40, 0.55),
    "skylight": (0.30, 0.45, 0.55), "pink": (0.80, 0.62, 0.52), "step": (0.72, 0.70, 0.66), "lakebed": (0.10, 0.14, 0.12),
    "wood": (0.55, 0.36, 0.20), "iron": (0.15, 0.15, 0.16), "s_wall": (0.88, 0.88, 0.86), "s_band": (0.96, 0.96, 0.95),
}
SHIRTS = [(0.85, 0.15, 0.12), (0.95, 0.95, 0.95), (0.12, 0.25, 0.55), (0.95, 0.75, 0.15), (0.20, 0.50, 0.30),
          (0.55, 0.25, 0.55), (0.15, 0.15, 0.17), (0.90, 0.50, 0.55), (0.55, 0.70, 0.85), (0.80, 0.40, 0.10)]
# ================================================================

def px2m(u, v): return ((u - PX0[0]) * M_PER_PX, (PX0[1] - v) * M_PER_PX)
def offset_line(line, d):
    """沿折线（可弯曲）平行偏移 d 米：正数在行进方向左侧"""
    from shapely.geometry import LineString
    g = LineString(line).offset_curve(d, join_style=2, mitre_limit=3.0)
    if g.geom_type != "LineString":
        g = max(getattr(g, "geoms", [g]), key=lambda q: q.length)
    return [tuple(p) for p in g.coords]

def road_list():
    """[(名称, 米坐标折线, 宽度)]；第 4 项为 "m" 的道路已经是米坐标，其余为卫星像素"""
    return [(r[0], list(r[1]) if len(r) > 3 else pxs(r[1]), r[2]) for r in ROADS]
def pxs(pts): return [px2m(u, v) for u, v in pts]

def lonlat2m(lon, lat):
    n = 2 ** 18
    x = (lon + 180) / 360 * n
    y = (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n
    return px2m((x - 219333) * 200, (y - 107265) * 200)

LAKE_LONLAT = [[121.20924,31.049511],[121.209328,31.04952],[121.209438,31.049429],[121.209558,31.049416],[121.209643,31.049444],[121.209813,31.049508],[121.209894,31.049526],[121.210004,31.049529],[121.21039,31.049508],[121.210606,31.049538],[121.210783,31.049562],[121.210921,31.049529],[121.211031,31.049456],[121.211059,31.049389],[121.211077,31.049268],[121.211034,31.049159],[121.21097,31.048983],[121.210963,31.048907],[121.210984,31.048795],[121.21102,31.048713],[121.211084,31.048628],[121.211168,31.048571],[121.211211,31.048434],[121.211232,31.048322],[121.211186,31.048246],[121.211112,31.048192],[121.211006,31.048152],[121.210917,31.048146],[121.210719,31.048213],[121.210595,31.048237],[121.210507,31.048225],[121.210376,31.048164],[121.210266,31.048131],[121.210163,31.048164],[121.210078,31.048176],[121.209894,31.048137],[121.209841,31.048098],[121.209724,31.048049],[121.209381,31.047949],[121.209236,31.047952],[121.209123,31.047982],[121.209048,31.048043],[121.20902,31.048101],[121.209024,31.048237],[121.209006,31.048464],[121.208995,31.048561],[121.20902,31.048658],[121.209056,31.04871],[121.209374,31.048831],[121.209395,31.048859],[121.209349,31.048937],[121.209293,31.049083],[121.209261,31.049229],[121.209226,31.049404]]

# 道路（拼图像素折线，宽度米）
ROADS = [
    ("Road_South", [(-150, 712), (900, 712)], 9.0),
    ("Boulevard", [(618, 548), (618, 1000)], 13.0),
    ("Road_North", [(478, 550), (900, 550)], 8.0),
    # 湖东侧道路：按新卫星图，自北向南略向东斜（单位：米）
    ("Road_LakeEast", [(-6.1, 215), (11.1, 65.5)], 6.0, "m"),     # 直线，与北侧道路斜交；两侧行道树对称
    ("Road_Service", [(548, 550), (548, 712)], 6.0),
]
# 广场 / 铺装（像素多边形）
PLAZAS = [
    ("Plaza_South", [(388, 701), (532, 701), (532, 708), (388, 708)]),
    ("Plaza_Between", [(380, 717), (505, 717), (505, 732), (380, 732)]),
    ("Plaza_EastWalk", [(528, 598), (544, 598), (544, 707), (528, 707)]),
    ("Yard", [(496, 452), (538, 452), (538, 544), (496, 544)]),     # 原误作停车场：普通铺装场地
]
# 相邻建筑（像素矩形 u0,v0,u1,v1，高度米）
NEIGHBORS = [
    ("North_A", (540, 378, 690, 420), 19), ("North_B", (540, 420, 582, 515), 19),
    ("North_C", (648, 420, 690, 515), 19), ("North_D", (582, 478, 648, 515), 15),
    ("Long_SW", (110, 780, 340, 822), 13),
    ("Shed_1", (496, 440, 536, 452), 4),
]
SOUTH_BLDG = ([(-61, -112), (18, -112), (18, -54), (-61, -54)], [9, 9, 9, 9], 6, 4.3)

TOP = LIB_FLOORS * LIB_FLOOR_H


# ---------------------------------------------------------------- 基础工具
def reset(): bpy.ops.wm.read_factory_settings(use_empty=True)

def get_coll(name, parent=None):
    c = bpy.data.collections.get(name) or bpy.data.collections.new(name)
    p = parent or bpy.context.scene.collection
    if c.name not in p.children: p.children.link(c)
    return c

_mats = {}
def mat(name, color=None, rough=None, metal=0.0):
    key = name
    if key in _mats: return _mats[key]
    col = color or COLORS.get(name, (0.8, 0.8, 0.8))
    m = bpy.data.materials.new(name); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*col, 1)
    b.inputs["Roughness"].default_value = rough if rough is not None else 0.8
    b.inputs["Metallic"].default_value = metal
    m.diffuse_color = (*col, 1)
    _mats[key] = m
    return m

def node_math(nt, op, a=None, b=None, v1=None, v2=None):
    n = nt.nodes.new("ShaderNodeMath"); n.operation = op
    if a is not None: nt.links.new(a, n.inputs[0])
    elif v1 is not None: n.inputs[0].default_value = v1
    if b is not None: nt.links.new(b, n.inputs[1])
    elif v2 is not None: n.inputs[1].default_value = v2
    return n.outputs[0]

def lit_emission(nt, b, cu, cv, mask, strength=None):
    """随机亮灯：每个格子（cu, cv）一个随机数，小于 LIT_WIN 的格子发暖光；mask = 只在窗玻璃处"""
    cmb = nt.nodes.new("ShaderNodeCombineXYZ"); nt.links.new(cu, cmb.inputs[0]); nt.links.new(cv, cmb.inputs[1])
    wn = nt.nodes.new("ShaderNodeTexWhiteNoise"); wn.noise_dimensions = "3D"; nt.links.new(cmb.outputs[0], wn.inputs["Vector"])
    on = node_math(nt, "LESS_THAN", wn.outputs["Value"], v2=LIT_WIN)
    if strength is None: strength = 4.5 * ART
    tint = nt.nodes.new("ShaderNodeMix"); tint.data_type = "RGBA"; nt.links.new(wn.outputs["Color"], tint.inputs["Factor"])
    tint.inputs["A"].default_value = (1.0, 0.72, 0.42, 1); tint.inputs["B"].default_value = (1.0, 0.90, 0.75, 1)   # 暖黄 ~ 暖白
    nt.links.new(tint.outputs["Result"], b.inputs["Emission Color"])
    nt.links.new(node_math(nt, "MULTIPLY", node_math(nt, "MULTIPLY", on, mask), v2=strength), b.inputs["Emission Strength"])

def glass_material():
    if "glass_core" in _mats: return _mats["glass_core"]
    m = bpy.data.materials.new("glass_core"); m.use_nodes = True
    nt = m.node_tree; b = nt.nodes["Principled BSDF"]
    uv = nt.nodes.new("ShaderNodeUVMap"); sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(uv.outputs["UV"], sep.inputs[0])
    gu = node_math(nt, "LESS_THAN", node_math(nt, "FRACT", node_math(nt, "DIVIDE", sep.outputs["X"], v2=0.75)), v2=0.12)
    gv = node_math(nt, "LESS_THAN", node_math(nt, "FRACT", node_math(nt, "DIVIDE", sep.outputs["Y"], v2=1.6)), v2=0.05)
    mx = node_math(nt, "MAXIMUM", gu, gv)
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"
    mix.inputs["A"].default_value = (*COLORS["glass"], 1); mix.inputs["B"].default_value = (*COLORS["mullion"], 1)
    nt.links.new(mx, mix.inputs["Factor"]); nt.links.new(mix.outputs["Result"], b.inputs["Base Color"])
    nt.links.new(node_math(nt, "MULTIPLY_ADD", mx, v2=0.5), b.inputs["Roughness"])
    nt.nodes[-1].inputs[2].default_value = 0.05
    b.inputs["Metallic"].default_value = 0.2
    if LIT_WIN > 0:   # 夜间：按"房间"（约 2 m 宽 × 一层高）随机亮灯
        lit_emission(nt, b, node_math(nt, "FLOOR", node_math(nt, "DIVIDE", sep.outputs["X"], v2=2.0)),
                     node_math(nt, "FLOOR", node_math(nt, "DIVIDE", sep.outputs["Y"], v2=LIB_FLOOR_H)), node_math(nt, "SUBTRACT", v1=1.0, b=mx))
    m.diffuse_color = (*COLORS["glass"], 1); _mats["glass_core"] = m
    return m

def facade_material():
    """相邻建筑：按物体坐标画出楼层窗带，屋顶面单独上色"""
    if "nb_facade" in _mats: return _mats["nb_facade"]
    m = bpy.data.materials.new("nb_facade"); m.use_nodes = True
    nt = m.node_tree; b = nt.nodes["Principled BSDF"]
    tc = nt.nodes.new("ShaderNodeTexCoord"); sep = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(tc.outputs["Object"], sep.inputs[0])
    geo = nt.nodes.new("ShaderNodeNewGeometry"); sn = nt.nodes.new("ShaderNodeSeparateXYZ")
    nt.links.new(geo.outputs["Normal"], sn.inputs[0])
    fz = node_math(nt, "FRACT", node_math(nt, "DIVIDE", sep.outputs["Z"], v2=3.8))
    band = node_math(nt, "MULTIPLY", node_math(nt, "GREATER_THAN", fz, v2=0.35), node_math(nt, "LESS_THAN", fz, v2=0.85))
    xy = node_math(nt, "ADD", sep.outputs["X"], sep.outputs["Y"])
    col = node_math(nt, "LESS_THAN", node_math(nt, "FRACT", node_math(nt, "DIVIDE", xy, v2=2.6)), v2=0.72)
    win = node_math(nt, "MULTIPLY", band, col)
    roof = node_math(nt, "GREATER_THAN", sn.outputs["Z"], v2=0.5)
    win = node_math(nt, "MULTIPLY", win, node_math(nt, "SUBTRACT", v1=1.0, b=roof))
    m1 = nt.nodes.new("ShaderNodeMix"); m1.data_type = "RGBA"
    m1.inputs["A"].default_value = (*COLORS["nb_wall"], 1); m1.inputs["B"].default_value = (*COLORS["nb_glass"], 1)
    nt.links.new(win, m1.inputs["Factor"])
    m2 = nt.nodes.new("ShaderNodeMix"); m2.data_type = "RGBA"
    nt.links.new(m1.outputs["Result"], m2.inputs["A"]); m2.inputs["B"].default_value = (*COLORS["nb_roof"], 1)
    nt.links.new(roof, m2.inputs["Factor"]); nt.links.new(m2.outputs["Result"], b.inputs["Base Color"])
    nt.links.new(node_math(nt, "MULTIPLY_ADD", win, v2=-0.6), b.inputs["Roughness"]); nt.nodes[-1].inputs[2].default_value = 0.8
    if LIT_WIN > 0:
        lit_emission(nt, b, node_math(nt, "FLOOR", node_math(nt, "DIVIDE", xy, v2=2.6)), node_math(nt, "FLOOR", node_math(nt, "DIVIDE", sep.outputs["Z"], v2=3.8)), win)
    m.diffuse_color = (*COLORS["nb_wall"], 1); _mats["nb_facade"] = m
    return m

def noisy_material(name, c1, c2, scale=0.08, rough=0.9, bump=0.0):
    """草地、铺装：两种颜色按噪声混合，减少大面积单色的塑料感"""
    if name in _mats: return _mats[name]
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; b = nt.nodes["Principled BSDF"]
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = scale
    nz.inputs["Detail"].default_value = 6
    nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    ramp = nt.nodes.new("ShaderNodeValToRGB")
    ramp.color_ramp.elements[0].position = 0.35; ramp.color_ramp.elements[0].color = (*c1, 1)
    ramp.color_ramp.elements[1].position = 0.7; ramp.color_ramp.elements[1].color = (*c2, 1)
    nt.links.new(nz.outputs["Fac"], ramp.inputs["Fac"]); nt.links.new(ramp.outputs["Color"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = rough
    if bump:
        nz2 = nt.nodes.new("ShaderNodeTexNoise"); nz2.inputs["Scale"].default_value = 2.5
        nt.links.new(tc.outputs["Object"], nz2.inputs["Vector"])
        bp = nt.nodes.new("ShaderNodeBump"); bp.inputs["Strength"].default_value = bump
        nt.links.new(nz2.outputs["Fac"], bp.inputs["Height"]); nt.links.new(bp.outputs["Normal"], b.inputs["Normal"])
    m.diffuse_color = (*c1, 1); _mats[name] = m
    return m

def water_material():
    if "water" in _mats: return _mats["water"]
    m = bpy.data.materials.new("water"); m.use_nodes = True
    nt = m.node_tree; b = nt.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*COLORS["water"], 1)
    b.inputs["Roughness"].default_value = 0.04
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 0.6; nz.inputs["Detail"].default_value = 8
    nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    bp = nt.nodes.new("ShaderNodeBump"); bp.inputs["Strength"].default_value = 0.08
    nt.links.new(nz.outputs["Fac"], bp.inputs["Height"]); nt.links.new(bp.outputs["Normal"], b.inputs["Normal"])
    m.diffuse_color = (*COLORS["water"], 1); _mats["water"] = m
    return m

def lake_bed_sloped(L, coll):
    """湖底网格：z 随离岸距离变深（0 m → -1.45，3 m → -2.0，10 m 以上 → -3.9）"""
    import shapely
    x0, y0, x1, y1 = L.bounds; st = 1.0
    xs = np.arange(x0 - 1, x1 + 1.01, st); ys = np.arange(y0 - 1, y1 + 1.01, st)
    X, Y = np.meshgrid(xs, ys); pts = shapely.points(X.ravel(), Y.ravel())
    d = shapely.distance(pts, L.exterior).reshape(X.shape); inside = shapely.contains_xy(L.buffer(0.6), X, Y)
    Z = np.where(d < 3, -1.45 - d / 3 * 0.55, -2.0 - np.minimum((d - 3) / 7, 1) * 1.9)
    idx = -np.ones(X.shape, int); V = []
    for j in range(X.shape[0]):
        for i in range(X.shape[1]):
            if inside[j, i] or (j + 1 < X.shape[0] and inside[j + 1, i]) or (i + 1 < X.shape[1] and inside[j, i + 1]):
                idx[j, i] = len(V); V.append((X[j, i], Y[j, i], Z[j, i]))
    F = [[idx[j, i], idx[j, i + 1], idx[j + 1, i + 1], idx[j + 1, i]] for j in range(X.shape[0] - 1) for i in range(X.shape[1] - 1)
         if min(idx[j, i], idx[j, i + 1], idx[j + 1, i + 1], idx[j + 1, i]) >= 0 and (inside[j, i] or inside[j + 1, i + 1])]
    return mesh_obj("LakeBed", V, F, lakebed_material(), coll, smooth=True)

def lakebed_material():
    """湖底：泥沙色 + 焦散亮纹（Voronoi 距边缘），只在浅水处透过水面可见"""
    m = bpy.data.materials.new("lakebed2"); m.use_nodes = True; nt = m.node_tree; b = nt.nodes["Principled BSDF"]
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 0.4; nt.links.new(tc.outputs["Object"], nz.inputs["Vector"])
    ramp = nt.nodes.new("ShaderNodeValToRGB"); e = ramp.color_ramp.elements
    e[0].color = (0.42, 0.40, 0.28, 1); e[1].color = (0.55, 0.52, 0.38, 1); nt.links.new(nz.outputs["Fac"], ramp.inputs["Fac"])
    vo = nt.nodes.new("ShaderNodeTexVoronoi"); vo.feature = "DISTANCE_TO_EDGE"; vo.inputs["Scale"].default_value = 1.4
    mp = nt.nodes.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1.0, 1.6, 1.0); nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
    nt.links.new(mp.outputs["Vector"], vo.inputs["Vector"])
    cr = nt.nodes.new("ShaderNodeMapRange"); cr.inputs["From Min"].default_value = 0.0; cr.inputs["From Max"].default_value = 0.06
    cr.inputs["To Min"].default_value = 1.0; cr.inputs["To Max"].default_value = 0.0; nt.links.new(vo.outputs["Distance"], cr.inputs["Value"])
    mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "ADD"
    nt.links.new(cr.outputs["Result"], mix.inputs["Factor"]); nt.links.new(ramp.outputs["Color"], mix.inputs["A"])
    mix.inputs["B"].default_value = (0.35, 0.36, 0.28, 1); nt.links.new(mix.outputs["Result"], b.inputs["Base Color"])
    b.inputs["Roughness"].default_value = 0.9; m.diffuse_color = (0.25, 0.24, 0.15, 1)
    return m

def water2_material(ripples=False):
    """透明湖水：折射 + 体积吸收（越深越绿蓝），表面细浪；ripples=True 时加顺风向的长条风纹"""
    m = bpy.data.materials.new("water2"); m.use_nodes = True; nt = m.node_tree; b = nt.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (1, 1, 1, 1); b.inputs["Roughness"].default_value = 0.01
    b.inputs["IOR"].default_value = 1.333; b.inputs["Transmission Weight"].default_value = 1.0
    tc = nt.nodes.new("ShaderNodeTexCoord")
    nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 1.2; nz.inputs["Detail"].default_value = 6
    nt.links.new(tc.outputs["Object"], nz.inputs["Vector"]); h = nz.outputs["Fac"]; strength = 0.05
    if ripples:
        mp = nt.nodes.new("ShaderNodeMapping"); mp.inputs["Rotation"].default_value = (0, 0, math.radians(35))
        mp.inputs["Scale"].default_value = (0.35, 2.6, 1.0); nt.links.new(tc.outputs["Object"], mp.inputs["Vector"])
        wv = nt.nodes.new("ShaderNodeTexNoise"); wv.inputs["Scale"].default_value = 1.6; wv.inputs["Detail"].default_value = 4
        nt.links.new(mp.outputs["Vector"], wv.inputs["Vector"])
        ad = nt.nodes.new("ShaderNodeMath"); ad.operation = "MULTIPLY_ADD"; ad.inputs[1].default_value = 0.3
        nt.links.new(nz.outputs["Fac"], ad.inputs[0]); nt.links.new(wv.outputs["Fac"], ad.inputs[2]); h = ad.outputs[0]; strength = 0.12
    bp = nt.nodes.new("ShaderNodeBump"); bp.inputs["Strength"].default_value = strength; bp.inputs["Distance"].default_value = 0.05
    nt.links.new(h, bp.inputs["Height"]); nt.links.new(bp.outputs["Normal"], b.inputs["Normal"])
    va = nt.nodes.new("ShaderNodeVolumeAbsorption"); va.inputs["Color"].default_value = (0.62, 0.84, 0.80, 1); va.inputs["Density"].default_value = 0.45
    vs_ = nt.nodes.new("ShaderNodeVolumeScatter"); vs_.inputs["Color"].default_value = (0.25, 0.55, 0.52, 1); vs_.inputs["Density"].default_value = 0.06
    ad_ = nt.nodes.new("ShaderNodeAddShader"); nt.links.new(va.outputs[0], ad_.inputs[0]); nt.links.new(vs_.outputs[0], ad_.inputs[1])
    nt.links.new(ad_.outputs[0], nt.nodes["Material Output"].inputs["Volume"])
    m.diffuse_color = (*COLORS["water"], 1); return m

def toon_water_material():
    """风格化：平涂青绿色，弱反射"""
    m = bpy.data.materials.new("water_toon"); m.use_nodes = True; b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (0.10, 0.42, 0.45, 1); b.inputs["Roughness"].default_value = 0.35
    b.inputs["Specular IOR Level"].default_value = 0.25; m.diffuse_color = (0.10, 0.42, 0.45, 1); return m

def mesh_obj(name, verts, faces, material, coll, uvs=None, smooth=False):
    me = bpy.data.meshes.new(name)
    me.from_pydata([tuple(map(float, v)) for v in verts], [], faces)
    me.validate(); me.update()
    if uvs is not None:
        layer = me.uv_layers.new(name="UVMap")
        for poly in me.polygons:
            for li in poly.loop_indices:
                layer.data[li].uv = uvs[me.loops[li].vertex_index]
    ob = bpy.data.objects.new(name, me)
    if material: me.materials.append(material)
    if smooth:
        for p in me.polygons: p.use_smooth = True
    coll.objects.link(ob)
    return ob

def fix_normals(ob):
    bm = bmesh.new(); bm.from_mesh(ob.data)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces); bm.to_mesh(ob.data); bm.free()

def ccw(ring):
    a = sum(ring[i][0] * ring[(i + 1) % len(ring)][1] - ring[(i + 1) % len(ring)][0] * ring[i][1] for i in range(len(ring)))
    return ring if a > 0 else ring[::-1]

def prism(name, ring, z0, z1, material, coll):
    ring = ccw(ring); n = len(ring)
    verts = [(x, y, z0) for x, y in ring] + [(x, y, z1) for x, y in ring]
    faces = [list(range(n))[::-1], list(range(n, 2 * n))] + [[i, (i + 1) % n, (i + 1) % n + n, i + n] for i in range(n)]
    return mesh_obj(name, verts, faces, material, coll)

def annulus(name, outer, inner, z0, z1, material, coll):
    n = len(outer)
    V = [(x, y, z0) for x, y in outer] + [(x, y, z1) for x, y in outer] + [(x, y, z0) for x, y in inner] + [(x, y, z1) for x, y in inner]
    O0, O1, I0, I1 = 0, n, 2 * n, 3 * n; F = []
    for i in range(n):
        j = (i + 1) % n
        F += [[O0 + i, O0 + j, O1 + j, O1 + i], [I0 + j, I0 + i, I1 + i, I1 + j],
              [O1 + i, O1 + j, I1 + j, I1 + i], [O0 + j, O0 + i, I0 + i, I0 + j]]
    return mesh_obj(name, V, F, material, coll)

def box(name, cx, cy, z0, sx, sy, sz, material, coll, rot=0.0):
    c, s = math.cos(rot), math.sin(rot)
    ring = [(cx + c * x - s * y, cy + s * x + c * y) for x, y in [(-sx/2, -sy/2), (sx/2, -sy/2), (sx/2, sy/2), (-sx/2, sy/2)]]
    return prism(name, ring, z0, z0 + sz, material, coll)

def rounded_polygon(pts, radii, seg=0.6, straight_seg=2.0):
    n = len(pts); arcs = []
    for i in range(n):
        p0, p1, p2 = Vector(pts[i - 1]), Vector(pts[i]), Vector(pts[(i + 1) % n]); r = radii[i]
        d1, d2 = (p0 - p1).normalized(), (p2 - p1).normalized()
        ang = math.acos(max(-1, min(1, d1.dot(d2)))); t = r / math.tan(ang / 2)
        c = p1 + (d1 + d2).normalized() * (r / math.sin(ang / 2)); a, b = p1 + d1 * t, p1 + d2 * t
        a0 = math.atan2((a - c).y, (a - c).x); a1 = math.atan2((b - c).y, (b - c).x)
        da = (a1 - a0 + math.pi) % (2 * math.pi) - math.pi; steps = max(3, int(abs(da) * r / seg))
        arcs.append([(c.x + r * math.cos(a0 + da * k / steps), c.y + r * math.sin(a0 + da * k / steps)) for k in range(steps + 1)])
    out = []
    for i in range(n):
        out += arcs[i]; a, b = Vector(arcs[i][-1]), Vector(arcs[(i + 1) % n][0])
        k = int((b - a).length / straight_seg); out += [tuple(a.lerp(b, (m + 1) / (k + 1))) for m in range(k)]
    return out

class Perimeter:
    def __init__(self, pts):
        P = np.array(ccw(list(pts)), float); self.P = P
        seg = np.roll(P, -1, 0) - P; L = np.linalg.norm(seg, axis=1); self.seg, self.L = seg, L
        self.S = np.concatenate([[0], np.cumsum(L)]); self.total = self.S[-1]
        sn = np.stack([seg[:, 1], -seg[:, 0]], 1) / L[:, None]
        vn = sn + np.roll(sn, 1, 0); vn /= np.linalg.norm(vn, axis=1)[:, None]
        cos = np.einsum("ij,ij->i", vn, sn); self.vn = vn / np.maximum(cos, 0.3)[:, None]
    def offset(self, d): return [tuple(p) for p in self.P + self.vn * d]
    def at(self, u):
        u = u % self.total; i = min(int(np.searchsorted(self.S, u, side="right") - 1), len(self.P) - 1)
        t = (u - self.S[i]) / self.L[i]; j = (i + 1) % len(self.P)
        return self.P[i] + self.seg[i] * t, self.vn[i] * (1 - t) + self.vn[j] * t
    def point(self, u, v, d):
        p, n = self.at(u); return (p[0] + n[0] * d, p[1] + n[1] * d, v)
    def u_of(self, x, y):
        q = np.array([x, y]); best = (1e9, 0)
        for i in range(len(self.P)):
            a = self.P[i]; t = np.clip(np.dot(q - a, self.seg[i]) / self.L[i] ** 2, 0, 1)
            dd = np.linalg.norm(a + self.seg[i] * t - q)
            if dd < best[0]: best = (dd, self.S[i] + t * self.L[i])
        return best[1]

def uv_prism(name, per, outline_uv, d_out, d_in, coll):
    n = len(outline_uv)
    V = [per.point(u, v, d_out) for u, v in outline_uv] + [per.point(u, v, d_in) for u, v in outline_uv]
    F = [list(range(n)), list(range(n, 2 * n))[::-1]] + [[i, i + n, (i + 1) % n + n, (i + 1) % n] for i in range(n)]
    ob = mesh_obj(name, V, F, None, coll)
    bm = bmesh.new(); bm.from_mesh(ob.data)
    bmesh.ops.triangulate(bm, faces=bm.faces); bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    bm.to_mesh(ob.data); bm.free()
    return ob

def pill_outline(u0, u1, v0, v1, k=10):
    r = (v1 - v0) / 2; vc = v0 + r
    pts = [(u1 - r + r * math.cos(a), vc + r * math.sin(a)) for a in np.linspace(-math.pi / 2, math.pi / 2, k)]
    return pts + [(u0 + r + r * math.cos(a), vc + r * math.sin(a)) for a in np.linspace(math.pi / 2, 3 * math.pi / 2, k)]

def arch_curve(uc, w, h, v0, k=40):
    return [(uc + x, v0 + h * (1 - (2 * x / w) ** 2)) for x in np.linspace(-w / 2, w / 2, k)]

def join_meshes(name, obs, coll):
    bm = bmesh.new()
    for ob in obs: bm.from_mesh(ob.data)
    me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
    for ob in obs: bpy.data.objects.remove(ob)
    new = bpy.data.objects.new(name, me); coll.objects.link(new); return new

def boolean(target, cutter):
    m = target.modifiers.new("cut_" + cutter.name, "BOOLEAN"); m.operation = "DIFFERENCE"; m.object = cutter; m.solver = "EXACT"


def profile_ring(name, per, prof, material, coll):
    """沿建筑轮廓扫掠一个闭合截面 prof = [(外偏距 d, 高度 z), ...]，得到带圆角线脚的环形实体"""
    rings = [per.offset(d) for d, z in prof]; n = len(rings[0]); m = len(prof)
    V = [(x, y, prof[k][1]) for k in range(m) for x, y in rings[k]]
    F = [[k * n + i, k * n + (i + 1) % n, ((k + 1) % m) * n + (i + 1) % n, ((k + 1) % m) * n + i] for k in range(m) for i in range(n)]
    ob = mesh_obj(name, V, F, material, coll, smooth=True); fix_normals(ob)
    ob.data.set_sharp_from_angle(angle=math.radians(50)) if hasattr(ob.data, "set_sharp_from_angle") else None
    return ob

def bullnose(d_in, d_out, z0, z1, r, k=5):
    """线脚截面：内侧平直，外缘上下两个圆角（半径 r）"""
    r = min(r, (z1 - z0) / 2 - 1e-3, d_out - d_in - 1e-3)
    pts = [(d_in, z0)]
    pts += [(d_out - r + r * math.cos(a), z0 + r + r * math.sin(a)) for a in np.linspace(-math.pi / 2, 0, k)]
    pts += [(d_out - r + r * math.cos(a), z1 - r + r * math.sin(a)) for a in np.linspace(0, math.pi / 2, k)]
    pts += [(d_in, z1)]
    return pts

def add_bevel(ob, width=0.06, seg=3, angle=35):
    if not DETAIL: return
    b = ob.modifiers.new("bevel", "BEVEL"); b.width = width; b.segments = seg
    b.limit_method = "ANGLE"; b.angle_limit = math.radians(angle); b.use_clamp_overlap = True; b.harden_normals = False
    for p in ob.data.polygons: p.use_smooth = True
    try: ob.data.set_sharp_from_angle(angle=math.radians(angle))
    except Exception: pass

def fin_mesh(name, per, fins, material, coll):
    """竖向遮阳片：fins = [(u, v0, v1)]，每片宽 0.07 m，从墙面内退 0.08 到 0.5 m"""
    V, F = [], []
    for u, v0, v1 in fins:
        b = len(V)
        for du in (-0.035, 0.035):
            for d in (-0.08, -0.5):
                V += [per.point(u + du, v0, d), per.point(u + du, v1, d)]
        F += [[b, b + 1, b + 3, b + 2], [b + 4, b + 6, b + 7, b + 5], [b, b + 4, b + 5, b + 1], [b + 2, b + 3, b + 7, b + 6], [b + 1, b + 5, b + 7, b + 3]]
    if not V: return None
    ob = mesh_obj(name, V, F, material, coll); fix_normals(ob); return ob

# ---------------------------------------------------------------- 胶囊窗立面建筑（图书馆 + 南侧建筑共用）
def facade_building(prefix, foot, radii, floors, fh, coll, arches=(), atrium=None, pattern=(7.5, 4.0, 10.0, 5.5, 8.5, 3.5, 6.5), gap=1.6, wall="cream", bandm="band"):
    cut = get_coll("Cutters"); cut.hide_render = True
    BT, BO, WT, CI = 0.8, 0.45, 0.35, 0.6
    ring = rounded_polygon(foot, radii); per = Perimeter(ring); top = floors * fh
    core = per.offset(-CI); n = len(core); S = list(per.S[:-1])
    V = [(x, y, 0) for x, y in core] + [(x, y, top) for x, y in core]
    F = [[i, (i + 1) % n, (i + 1) % n + n, i + n] for i in range(n)] + [list(range(n))[::-1], list(range(n, 2 * n))]
    mesh_obj(f"{prefix}_GlassCore", V, F, glass_material(), coll, uvs=[(s, 0) for s in S] + [(s, top) for s in S])
    band_ring = per.offset(BO); slabs = []
    for k in range(floors + 1):
        z = k * fh
        z0, z1 = (0, BT * 0.6) if k == 0 else (z - BT / 2, z + BT / 2)
        if k == floors: z1 = z + (BT * 1.6 if DETAIL else BT)          # 女儿墙加高
        if DETAIL:
            bo = BO + (0.12 if 0 < k < floors else 0.0)                    # 中间楼层线脚更挑出
            slabs.append(profile_ring(f"{prefix}_Band_{k}", per, bullnose(-CI + 0.05, bo, z0, z1, 0.32 if k else 0.12), mat(bandm), coll))
        else:
            slabs.append(prism(f"{prefix}_Band_{k}", band_ring, z0, z1, mat(bandm), coll))
    inner = per.offset(-WT)
    atr = None
    if atrium:
        (ax, ay), aw = atrium; uc = per.u_of(ax, ay); atr = (uc - aw / 2, uc + aw / 2)
    arch_u = [(per.u_of(x, y), w, h) for (x, y), w, h in arches]
    walls = []; fins = []
    in_arch = lambda u, v: any(abs(u - uc) < aw / 2 + 0.6 and v < ah + BT * 0.6 + 0.4 for uc, aw, ah in arch_u)
    for f in range(floors):
        z0 = BT * 0.6 if f == 0 else f * fh + BT / 2; z1 = (f + 1) * fh - BT / 2
        w = annulus(f"{prefix}_Wall_{f}", ring, inner, z0, z1, mat(wall), coll); walls.append(w)
        v0, v1 = z0 + 0.35, z1 - 0.35; pills = []; u = 1.2 + (f * 2.3) % 4; idx = f * 2
        while u < per.total - 1.5:
            L = pattern[idx % len(pattern)]; idx += 1; u1 = min(u + L, per.total - 1.5)
            skip = atr is not None and f >= 2 and (u1 > atr[0] - 0.8 and u < atr[1] + 0.8)
            if u1 - u > (v1 - v0) + 0.3 and not skip:
                pills.append(uv_prism(f"{prefix}p{f}_{len(pills)}", per, pill_outline(u, u1, v0, v1), 0.9, -0.5, cut))
                if DETAIL:                                           # 窗内竖向遮阳片，间距约 0.75 m
                    r = (v1 - v0) / 2; vc = v0 + r; nf = max(1, int((u1 - u - 0.5) / 0.75))
                    for q in range(nf + 1):
                        fu = u + 0.25 + (u1 - u - 0.5) * q / max(nf, 1)
                        e = max(0.0, r - (fu - u), r - (u1 - fu)); h = math.sqrt(max(r * r - e * e, 0)) - 0.04
                        if h > 0.25 and not in_arch(fu, vc): fins.append((fu, vc - h, vc + h))
            u = u1 + gap
        if atr is not None and f >= 2:
            pills.append(uv_prism(f"{prefix}atr{f}", per, [(atr[0], z0 - 0.1), (atr[1], z0 - 0.1), (atr[1], z1 + 0.1), (atr[0], z1 + 0.1)], 0.9, -0.5, cut))
        if pills:
            cutter = join_meshes(f"{prefix}_Cut_F{f}", pills, cut); cutter.display_type = "WIRE"; boolean(w, cutter)
    if DETAIL:
        fin_mesh(f"{prefix}_Fins", per, fins, mat("fin", (0.92, 0.91, 0.88), 0.45), coll)
    v_base = BT * 0.6
    for ai, (uc, aw, ah) in enumerate(arch_u):
        ac = uv_prism(f"{prefix}_Cut_Arch{ai}", per, arch_curve(uc, aw, ah, v_base), 1.2, -CI + 0.05, cut); ac.display_type = "WIRE"
        for f in range(floors):
            if f * fh < ah + v_base: boolean(walls[f], ac)
        for k in range(1, floors):
            if k * fh < ah + v_base: boolean(slabs[k], ac)
        # 拱门框
        pts = arch_curve(uc, aw + 0.2, ah + 0.1, v_base, 60); V, F = [], []
        for i, (u, v) in enumerate(pts):
            a = pts[max(i - 1, 0)]; b = pts[min(i + 1, len(pts) - 1)]
            tu, tv = b[0] - a[0], b[1] - a[1]; ln = math.hypot(tu, tv); nu, nv = tv / ln, -tu / ln
            if (u - uc) * nu + (v - v_base - ah / 2) * nv < 0: nu, nv = -nu, -nv
            prof = [(0.0, -0.4), (1.3, -0.4), (1.3, 0.9), (0.0, 0.9)] if DETAIL else [(0.0, -0.4), (0.75, -0.4), (0.75, 0.6), (0.0, 0.6)]
            for s, d in prof: V.append(per.point(u + nu * s, v + nv * s, d))
        m = len(pts)
        for i in range(m - 1):
            for k in range(4):
                a, b = i * 4 + k, i * 4 + (k + 1) % 4; F.append([a, b, b + 4, a + 4])
        F += [[0, 1, 2, 3], [(m - 1) * 4 + 3, (m - 1) * 4 + 2, (m - 1) * 4 + 1, (m - 1) * 4]]
        fr = mesh_obj(f"{prefix}_ArchFrame{ai}", V, F, mat("band"), coll); fix_normals(fr); add_bevel(fr, 0.12, 4, 30)
    for w in walls: add_bevel(w, 0.07, 3, 35)                       # 胶囊窗洞口、墙角倒圆
    ptop = top + (BT * 1.6 if DETAIL else BT)
    prism(f"{prefix}_RoofDeck", per.offset(-0.3), ptop - 0.55 if DETAIL else ptop, (ptop - 0.55 if DETAIL else ptop) + 0.05, mat("roof"), coll)
    return per, (ptop - 0.5) if DETAIL else ptop + 0.05


def build_library():
    lib = get_coll("Library")
    per, rz = facade_building("Lib", LIB_FOOT, LIB_RADII, LIB_FLOORS, LIB_FLOOR_H, lib, arches=LIB_ARCHES, atrium=LIB_ATRIUM)
    # 屋顶：按卫星图——南翼西段 3×3 天窗、交接处设备间、东翼网格铺装
    for i in range(3):
        for j in range(3):
            x, y = -31 + i * 4.2, -22 + j * 4.2
            box(f"Skylight_{i}{j}", x, y, rz, 2.8, 2.8, 0.25, mat("band"), lib)
            box(f"SkylightGlass_{i}{j}", x, y, rz + 0.25, 2.4, 2.4, 1.0, mat("skylight", rough=0.1), lib)
    box("RoofPlant", 2, -16, rz, 16, 10, 3.6, mat("cream"), lib)
    box("RoofPlant_cap", 2, -16, rz + 3.6, 16.6, 10.6, 0.35, mat("band"), lib)
    box("RoofPlant2", -12, -20, rz, 7, 6, 2.4, mat("roof2"), lib)
    for k in range(5):
        box(f"AC_{k}", -20 + k * 3.2, -9, rz, 2.2, 1.6, 1.2, mat("roof2"), lib)
    # 东翼屋顶铺装网格（细线）
    for k in range(9):
        box(f"RoofGridX_{k}", 24, -2 + k * 4, rz + 0.01, 28, 0.15, 0.04, mat("roof2"), lib)
    for k in range(7):
        box(f"RoofGridY_{k}", 12 + k * 4, 14, rz + 0.01, 0.15, 34, 0.04, mat("roof2"), lib)
    for ob in lib.objects:
        if ob.name.startswith(("Skylight", "RoofPlant", "AC_")): add_bevel(ob, 0.05, 2, 40)
    lib["rz"] = rz
    # 兴趣点（3D 坐标）
    def arch_pt(i, h=0.5):
        (x, y), w, ah = LIB_ARCHES[i]; u = per.u_of(x, y); return list(per.point(u, h, 1.5))
    lib["poi_north"] = arch_pt(0, 1.0); lib["poi_south"] = arch_pt(1, 1.0); lib["poi_corner"] = arch_pt(2, 1.0)
    (ax, ay), _ = LIB_ATRIUM; lib["poi_atrium"] = list(per.point(per.u_of(ax, ay), 17, 1.0))
    lib["poi_roof"] = [-27, -18, rz + 2]
    return per


def build_neighbors():
    nb = get_coll("Neighbors")
    for name, (u0, v0, u1, v1), h in NEIGHBORS:
        a = px2m(u0, v1); b = px2m(u1, v0)
        ob = prism(name, [(a[0], a[1]), (b[0], a[1]), (b[0], b[1]), (a[0], b[1])], 0, h, facade_material(), nb)
        prism(name + "_parapet", [(a[0] + .3, a[1] + .3), (b[0] - .3, a[1] + .3), (b[0] - .3, b[1] - .3), (a[0] + .3, b[1] - .3)], h, h + 0.9, mat("nb_roof", COLORS["nb_wall"]), nb)
    foot, radii, floors, fh = SOUTH_BLDG
    facade_building("South", foot, radii, floors, fh, nb, pattern=(9.0, 5.0, 12.0, 6.5), gap=2.0, wall="s_wall", bandm="s_band")


# ---------------------------------------------------------------- 场地
class Path2:
    def __init__(self, pts): self.p = [tuple(map(float, q)) for q in pts]
    def contains(self, x, y):
        inside = False; p = self.p; j = len(p) - 1
        for i in range(len(p)):
            xi, yi = p[i]; xj, yj = p[j]
            if (yi > y) != (yj > y) and x < (xj - xi) * (y - yi) / (yj - yi + 1e-12) + xi: inside = not inside
            j = i
        return inside

def catmull(points, k=10, closed=True):
    P = [Vector(p) for p in points]; n = len(P); out = []
    for i in (range(n) if closed else range(n - 1)):
        p0, p1, p2, p3 = P[i - 1], P[i], P[(i + 1) % n], P[(i + 2) % n]
        if not closed: p0 = P[max(i - 1, 0)]; p2 = P[min(i + 1, n - 1)]; p3 = P[min(i + 2, n - 1)]
        for t in np.linspace(0, 1, k, endpoint=False):
            out.append(tuple(0.5 * ((2 * p1) + (-p0 + p2) * t + (2 * p0 - 5 * p1 + 4 * p2 - p3) * t * t + (-p0 + 3 * p1 - 3 * p2 + p3) * t ** 3)))
    if not closed: out.append(tuple(P[-1]))
    return out

def ribbon(name, line, width, z, material, coll, closed=False):
    P = np.array(line, float); n = len(P)
    T = np.roll(P, -1, 0) - np.roll(P, 1, 0)
    if not closed: T[0] = P[1] - P[0]; T[-1] = P[-1] - P[-2]
    T /= np.linalg.norm(T, axis=1)[:, None]; Nn = np.stack([-T[:, 1], T[:, 0]], 1)
    L = P + Nn * width / 2; R = P - Nn * width / 2
    V = [(x, y, z) for x, y in L] + [(x, y, z) for x, y in R]
    m = n if closed else n - 1
    return mesh_obj(name, V, [[n + i, n + (i + 1) % n, (i + 1) % n, i] for i in range(m)], material, coll)

def dist_to_line(x, y, line):
    best = 1e9
    for (ax, ay), (bx, by) in zip(line[:-1], line[1:]):
        dx, dy = bx - ax, by - ay; L2 = dx * dx + dy * dy or 1e-9
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / L2))
        best = min(best, math.hypot(ax + t * dx - x, ay + t * dy - y))
    return best

def resample(line, step):
    out = []
    for (ax, ay), (bx, by) in zip(line[:-1], line[1:]):
        L = math.hypot(bx - ax, by - ay); k = max(1, int(L / step))
        for i in range(k): out.append((ax + (bx - ax) * i / k, ay + (by - ay) * i / k, math.atan2(by - ay, bx - ax)))
    return out

SITE = {}

def build_site(lib_per):
    site = get_coll("Site")
    pad = 500
    prism("Ground", [(SCENE_X[0] - pad, SCENE_Y[0] - pad), (SCENE_X[1] + pad, SCENE_Y[0] - pad),
                     (SCENE_X[1] + pad, SCENE_Y[1] + pad), (SCENE_X[0] - pad, SCENE_Y[1] + pad)], -1, 0,
          noisy_material("grass", COLORS["grass"], COLORS["grass2"], 0.03, 0.95, 0.15), site)

    ground = bpy.data.objects["Ground"]
    from shapely.geometry import Polygon, LineString, Point
    from shapely.ops import unary_union
    MITRE = dict(join_style=2, mitre_limit=3.0)
    lake = ccw(catmull([lonlat2m(*p) for p in LAKE_LONLAT], k=4))
    lper = Perimeter(lake)
    L = Polygon(lake).buffer(0)
    # 紧贴湖东岸的道路：环湖步道与台阶在这里让位
    K = unary_union([LineString(pts).buffer(w / 2 + 0.3, cap_style=2) for name, pts, w in road_list() if name == "Road_LakeEast"])
    S0 = unary_union([L.buffer(RING_W, **MITRE), Polygon(PINK_SUNKEN)]).difference(K).union(L.buffer(0.6))   # 下沉区（沿湖步道层）
    TW = STEP_D * STEP_N
    ZE, ZS, ZST = Polygon(STEP_ZONES[0]), Polygon(STEP_ZONES[1]), Polygon(STEP_ZONES[2])
    ZW = Polygon(WALL_ZONE)
    Z = unary_union([ZE, ZS, ZST])
    NOTRANS = L.buffer(0.3)                                                    # 过渡带不能伸进水面
    def band(k): return S0.buffer(STEP_D * (k + 1), **MITRE).difference(S0.buffer(STEP_D * k, **MITRE) if k else S0).difference(K).difference(NOTRANS)
    # 台阶：东岸第 k 级只在 y >= 48 + (k+1)*4 的范围出现（逐级收窄）；南岸两处全宽
    t0, t1 = EAST_TAPER
    step_bands = []
    for k in range(STEP_N):
        ycut = t0 + (t1 - t0) * (k + 1) / STEP_N
        east = band(k).intersection(ZE).intersection(Polygon([(-300, ycut), (300, ycut), (300, 400), (-300, 400)]))
        rest = band(k).intersection(unary_union([ZS, ZST])).difference(ZE)
        step_bands.append(unary_union([east, rest]))
    NS = 6
    slope_bands = [S0.buffer(TW * (k + 1) / NS, **MITRE).difference(S0.buffer(TW * k / NS, **MITRE) if k else S0).difference(K).difference(NOTRANS)
                   .difference(Z).difference(ZW) for k in range(NS)]
    G = unary_union([S0, L.buffer(0.35)] + step_bands + slope_bands)                                      # 低于地面的全部范围
    cut = get_coll("Cutters"); cut.hide_render = True
    for i, g in enumerate(getattr(G, "geoms", [G])):
        g = Polygon(g.exterior).buffer(0.02, join_style=2).simplify(0.05)          # 清理轮廓，避免布尔运算失败
        hole = prism(f"LakeHole_{i}", list(g.exterior.coords)[:-1], -4, 2, None, cut); hole.display_type = "WIRE"; boolean(ground, hole)
    step_m, grass_m = mat("step"), noisy_material("grass", None, None)
    # 水面、湖底、驳岸压顶
    if WATER in (1, 2):
        lake_bed_sloped(L, site)                                          # 岸边浅、湖心深的湖底
        prism("Lake", lake, -4.2, WATER_Z, water2_material(ripples=WATER == 2), site)
    else:
        prism("LakeBed", lake, -4, -2.4, mat("lakebed"), site)
        prism("Lake", lake, -2.4, WATER_Z, water_material() if WATER != 3 else toon_water_material(), site)
        if WATER == 3:                                                    # 风格化：岸边两道白色细线
            for k, (d, w) in enumerate([(0.9, 0.22), (2.3, 0.12)]):
                ring_ = L.buffer(-d).exterior.buffer(w / 2) if not L.buffer(-d).is_empty else None
                if ring_ is not None: poly_obj(f"Foam_{k}", ring_, WATER_Z, WATER_Z + 0.01, mat("foam", (0.93, 0.97, 0.97), 0.6), site)
    poly_obj("G_Coping", L.buffer(0.35).difference(L), -2.4, RING_Z + 0.06, mat("shore"), site)
    # 沿湖步道（下沉层，整圈连续）与下沉粉色广场
    pathm = noisy_material("ringp", (0.74, 0.73, 0.70), (0.68, 0.67, 0.64), 0.2, 0.85)
    poly_obj("G_Ring", S0.difference(L.buffer(0.35)), -2.4, RING_Z, pathm, site)
    pink = noisy_material("pinkp", COLORS["pink"], (0.76, 0.58, 0.50), 0.25, 0.85)
    poly_obj("G_PinkSunken", Polygon(PINK_SUNKEN).intersection(S0).difference(L.buffer(0.6)).difference(K), RING_Z, RING_Z + 0.01, pink, site)
    # 台阶与草坡（湖东南角 WALL_ZONE 内两者都没有：步道背后直接是挡土墙）
    steps_top = []
    for k, b in enumerate(step_bands):
        top = RING_Z * (1 - (k + 1) / STEP_N)
        poly_obj(f"G_Step{k}", b, -2.4, top, step_m, site); steps_top.append(top)
    for k, b in enumerate(slope_bands):
        poly_obj(f"G_Slope{k}", b, -2.4, RING_Z * (1 - (k + 1) / NS), grass_m, site)
    # 挡土墙压顶（一道窄的石质收边，强调高差）
    wall_line = S0.exterior if S0.geom_type == "Polygon" else max(S0.geoms, key=lambda g: g.area).exterior
    poly_obj("G_WallCap", wall_line.buffer(0.25).intersection(ZW).difference(S0).difference(NOTRANS), -1, 0.08, mat("shore"), site)
    # 东岸台阶上方的地面步道
    paving = noisy_material("pav", COLORS["paving"], (0.62, 0.60, 0.57), 0.3, 0.8)
    UPPER_W = 2.5
    up_zone = Polygon([(-45, t1 - 2), (25, t1 - 2), (25, 240), (-45, 240)])
    poly_obj("G_UpperEast", G.buffer(UPPER_W, **MITRE).difference(G).intersection(up_zone).difference(K.buffer(1.0)), -1, 0.055, paving, site)
    # 粉色广场外圈、长条铺装带（地面高度，挖掉下沉部分）
    poly_obj("G_PinkOuter", Polygon(PINK_OUTER).difference(G), -1, 0.05, pink, site)
    poly_obj("G_Linear", Polygon(LINEAR_PLAZA).difference(G).difference(Polygon(PINK_OUTER)), -1, 0.048, paving, site)
    GB = G.buffer(0.05)

    # 道路与人行道（人行道遇到下沉区自动截断）
    roads = get_coll("Roads", site); asphalt = noisy_material("asph", COLORS["asphalt"], (0.29, 0.30, 0.32), 0.1, 0.7)
    road_lines = []
    RL = road_list()
    road_polys = [LineString(line).buffer(w / 2, cap_style=2, join_style=2) for name, line, w in RL]
    U_road = unary_union(road_polys)
    U_road = U_road.buffer(4.0, join_style=1).buffer(-4.0, join_style=1)            # 路口内角倒圆（半径约 4 m）
    poly_obj("Roads", U_road, -1, 0.06, asphalt, roads)
    WALK_W = 2.6
    U_side = unary_union([p for (name, _, _), p in zip(RL, road_polys) if name != "Road_LakeEast"])
    U_side = U_side.buffer(4.0, join_style=1).buffer(-4.0, join_style=1)
    sidewalk = U_side.buffer(WALK_W + 0.2, join_style=1).difference(U_road.buffer(0.2))
    east = [p for (name, _, _), p in zip(RL, road_polys) if name == "Road_LakeEast"][0]
    sidewalk = sidewalk.difference(east.buffer(0.3, cap_style=2)).difference(GB)    # 湖东侧道路两侧不设人行道
    poly_obj("Sidewalks", sidewalk, -1, 0.1, mat("paving"), roads)
    SITE["sidewalk"] = sidewalk; SITE["road_union"] = U_road
    for name, line, w in RL:
        road_lines.append((line, w))
        if w >= 8:
            for x, y, a in resample(line, 8)[1:]:
                box("dash", x, y, 0.085, 3.0, 0.25, 0.02, mat("band"), roads, rot=a)
    # 广场（卫星像素）与新卫星图描绘的广场（米）
    for name, poly in PLAZAS:
        prism(name, pxs(poly), -1, 0.045, paving, site)
    for i, (name, poly, m) in enumerate(PLAZAS_M):
        if name == "Plaza_LibWest": continue                        # 并入图书馆门前整片铺装
        poly_obj(name, Polygon(poly).difference(GB), -1, 0.048 + 0.003 * i, pink if m == "pink" else paving, site)
    # 图书馆周边铺装：外扩 5 m 的环形
    apron = Perimeter(rounded_polygon(LIB_FOOT, LIB_RADII)).offset(5.0)
    # 图书馆门前整片水泥铺装：建筑外扩 5 m + 弧形路以内的区域 + 弧形路本身 + 西侧小广场，合并成一整块（无接缝）
    n_arch = LIB_ARCHES[0][0]
    ne = [(-23.5, 4.6), (-23.5, 30)]                                                       # 弧形路 → 南岸台阶顶
    fill = Polygon([(-50, 10)] + [tuple(p) for p in LIB_ARC[1:]] + [(0.6, 37.75), (7.6, 37.75), (7.6, -2.47), (-45.5, -2.47), (-50, 2)])
    court = unary_union([Polygon(rounded_polygon(LIB_FOOT, LIB_RADII)).buffer(5.0, join_style=1), fill.buffer(0),
                         LineString(LIB_ARC).buffer(2.0, cap_style=2, join_style=1),
                         Polygon(PLAZAS_M[1][1]),
                         LineString([(n_arch[0], n_arch[1] + 0.5), (n_arch[0], 4.6)]).buffer(3.0, cap_style=2)])
    court = court.buffer(0.6, join_style=1).buffer(-0.6, join_style=1).difference(GB)     # 圆顺凹角、去掉缝隙
    poly_obj("LibApron", court, -1, 0.06, paving, site); SITE["court"] = court
    poly_obj("Walk_NorthEntry", LineString(ne).buffer(3.0, cap_style=2).difference(GB).difference(court), -1, 0.06, paving, site)
    # 新卫星图上的步道网（截掉落在下沉区里的部分）
    paths = []
    for i, (pts, w) in enumerate(PATHS):
        geom = LineString(pts).buffer(w / 2, cap_style=2, join_style=2).difference(GB)
        m_ = noisy_material("arcp", (0.55, 0.54, 0.52), (0.50, 0.49, 0.47), 0.3, 0.8) if pts is LIB_ARC else noisy_material("pathg", (0.70, 0.70, 0.68), (0.64, 0.64, 0.62), 0.3, 0.85)
        if pts is not LIB_ARC: poly_obj(f"Walk_{i}", geom, -1, 0.052 + 0.0015 * i, m_, site)
        paths.append((pts, w))

    # ---- 给动画用的步行网络（带高度）
    def lines_of(geom):
        if geom.is_empty: return []
        if geom.geom_type == "LineString": return [list(geom.coords)]
        return [list(g.coords) for g in getattr(geom, "geoms", []) if g.geom_type == "LineString" and g.length > 3]
    walks = {}
    ringline = L.buffer(1.9).exterior
    prom = [ringline.interpolate(d) for d in np.arange(0, ringline.length, 2.0)]
    walks["prom"] = [(p.x, p.y, RING_Z) for p in prom] + [(prom[0].x, prom[0].y, RING_Z)]
    for i, (pts, w) in enumerate(PATHS):
        for j, seg in enumerate(lines_of(LineString(pts).difference(G.buffer(0.8)))): walks[f"path{i}_{j}"] = [(x, y, 0) for x, y in seg]
    for j, seg in enumerate(lines_of(LineString(ne).difference(G.buffer(0.3)))): walks[f"northEntry_{j}"] = [(x, y, 0) for x, y in seg]
    upper = G.buffer(UPPER_W / 2, **MITRE).exterior.intersection(up_zone).difference(K.buffer(2.0))
    for j, seg in enumerate(lines_of(upper)): walks[f"upperEast_{j}"] = [(x, y, 0) for x, y in seg]
    lin = LineString([(-127, 16), (-80, 16)]).difference(G.buffer(0.8))
    for j, seg in enumerate(lines_of(lin)): walks[f"linear_{j}"] = [(x, y, 0) for x, y in seg]
    # 楼梯：台阶区每隔约 16 m 一处，从环湖步道走上地面
    stairs = []
    zb = G.exterior if G.geom_type == "Polygon" else unary_union([g.exterior for g in G.geoms])
    zb = zb.intersection(unary_union([ZE.intersection(up_zone), ZS, ZST])).difference(K.buffer(3.0))
    for seg in lines_of(zb):
        ls = LineString(seg)
        for d in np.arange(6, ls.length - 3, 16):
            p = ls.interpolate(d); q = L.exterior.interpolate(L.exterior.project(p))
            v = np.array([p.x - q.x, p.y - q.y]); dist = np.linalg.norm(v)
            if dist > RING_W + TW + 1.5 or dist < 1: continue
            v /= dist; a = np.array([q.x, q.y]) + v * 1.9; b = np.array([p.x, p.y]) + v * 1.2
            stairs.append(((a[0], a[1], RING_Z), (b[0], b[1], 0.0)))
    # 粉色广场：从下沉广场中间走向南侧台阶，上到东西向步道
    stairs.append(((-66.5, 20, RING_Z), (-66.5, 10, 0.0)))
    walks["pinkIn"] = [(-66.5, 20, RING_Z), (-66.5, 36, RING_Z)]
    # 草坡处：几条连接步道末端与环湖步道的小路
    for P0 in ((-205, -10), (-134.5, 24), (-12, 87), (-14, 139), (-190, -10)):
        p = Point(P0); q = ringline.interpolate(ringline.project(p))
        if p.distance(q) < 25: stairs.append(((P0[0], P0[1], 0.0), (q.x, q.y, RING_Z)))
    for i, (a, b) in enumerate(stairs): walks[f"stair{i}"] = [a, b]
    for k_, v_ in walks.items(): walks[k_] = [(round(x, 2), round(y, 2), round(z, 3)) for x, y, z in v_]

    # 可以坐的台阶（中间一级，面朝湖）
    terr_seats = []
    mid = S0.buffer(STEP_D * 1.5, **MITRE).exterior
    for seg in lines_of(mid.intersection(unary_union([ZE.intersection(up_zone), ZS, ZST])).difference(K.buffer(3.0))):
        ls = LineString(seg)
        for d in np.arange(1.5, ls.length - 1.5, 1.6):
            p = ls.interpolate(d); q = L.exterior.interpolate(L.exterior.project(p))
            if any(math.hypot(p.x - a[0], p.y - a[1]) < 2.5 for a, b in stairs): continue
            if p.distance(q) > RING_W + TW: q = S0.exterior.interpolate(S0.exterior.project(p))   # 粉色广场的台阶：面朝广场
            face = math.atan2(q.y - p.y, q.x - p.x)
            terr_seats.append((round(p.x, 2), round(p.y, 2), round(steps_top[1], 3), round(face, 3)))

    benches = build_benches(L, S0, G, stairs)
    SITE.update(lake=lake, roads=road_lines, paths=paths, lib_ring=lib_per.offset(0), lper=lper,
                benches=benches, terr_seats=terr_seats, walks=walks, G=G, S0=S0)
    return site


def poly_obj(name, geom, z0, z1, material, coll):
    """把 shapely 多边形（可带洞）挤出成实体；洞用布尔运算挖掉"""
    parts = [geom] if geom.geom_type == "Polygon" else [g for g in getattr(geom, "geoms", []) if g.geom_type == "Polygon"]
    cut = get_coll("Cutters"); out = []
    for i, g in enumerate(parts):
        if g.area < 0.05: continue
        g = g.simplify(0.03)
        if g.is_empty or g.geom_type != "Polygon": continue
        ob = prism(f"{name}_{i}", list(g.exterior.coords)[:-1], z0, z1, material, coll)
        for j, h in enumerate(g.interiors):
            c = prism(f"{name}_{i}_h{j}", list(h.coords)[:-1], z0 - 1, z1 + 1, None, cut); c.display_type = "WIRE"
            boolean(ob, c)
        out.append(ob)
    return out


def build_benches(L, S0, G, stairs):
    """长椅：环湖步道外侧（面朝湖）、长条铺装带、下沉粉色广场、图书馆北入口。返回 (x, y, 朝向, 底面高度)"""
    from shapely.geometry import Point
    coll = get_coll("Benches", get_coll("Site"))
    bm = bmesh.new()
    def cube(c, sz): bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(c) @ Matrix.Diagonal((*sz, 1)))
    cube((0, 0, 0.43), (1.8, 0.45, 0.06)); cube((0, -0.24, 0.72), (1.8, 0.06, 0.38))
    tmp = bpy.data.meshes.new("Bench"); bm.to_mesh(tmp); bm.free()
    bm2 = bmesh.new()
    for x in (-0.75, 0.75):
        bmesh.ops.create_cube(bm2, size=1.0, matrix=Matrix.Translation((x, 0, 0.2)) @ Matrix.Diagonal((0.08, 0.45, 0.42, 1)))
    legs = bpy.data.meshes.new("legs"); bm2.to_mesh(legs); bm2.free()
    bm3 = bmesh.new(); bm3.from_mesh(tmp); off = len(bm3.faces); bm3.from_mesh(legs)
    for i, f in enumerate(bm3.faces): f.material_index = 0 if i < off else 1
    me = bpy.data.meshes.new("Bench"); bm3.to_mesh(me); bm3.free(); bpy.data.meshes.remove(tmp); bpy.data.meshes.remove(legs)
    me.materials.append(mat("wood", rough=0.7)); me.materials.append(mat("iron", rough=0.5))
    out = []
    def put(x, y, face, z0):
        ob = bpy.data.objects.new("Bench", me); coll.objects.link(ob)
        ob.location = (x, y, z0 + 0.06); ob.rotation_euler = (0, 0, face - math.pi / 2)
        out.append((round(x, 2), round(y, 2), round(face, 3), round(z0, 3)))
    ring_outer = L.buffer(RING_W - 0.8).exterior
    for d in np.arange(10, ring_outer.length, 30):
        p = ring_outer.interpolate(d)
        if not S0.buffer(-0.3).contains(p): continue
        if not (SCENE_X[0] < p.x < SCENE_X[1] and SCENE_Y[0] < p.y < SCENE_Y[1]): continue
        if any(math.hypot(p.x - a[0], p.y - a[1]) < 4 for a, b in stairs): continue
        q = L.exterior.interpolate(L.exterior.project(p))
        put(p.x, p.y, math.atan2(q.y - p.y, q.x - p.x), RING_Z)
    for x in (-120, -108, -96, -88):                                     # 长条铺装带，面朝湖
        p = Point(x, 15.5)
        if not G.buffer(0.6).contains(p): put(x, 15.5, math.pi / 2, 0.0)
    for x in (-72, -61): put(x, 18.0, math.pi / 2, RING_Z)               # 下沉粉色广场里，面朝湖
    for x, y in ((-12, 9.2), (-30, 9.2)): put(x, y, math.pi / 2, 0.0)                     # 图书馆门前弧形路旁
    return out


# ---------------------------------------------------------------- 树（5 种素材，随机散布 + 行道树）
def jitter_mesh(bm, amt, rnd):
    for v in bm.verts: v.co += Vector((rnd.uniform(-amt, amt), rnd.uniform(-amt, amt), rnd.uniform(-amt, amt)))

def tree_assets():
    """第二版风格化树木（trees2.py：融球树冠 + 平滑着色）；旧版低多边形树见 tree_assets_v1()"""
    import importlib, sys as _s
    _s.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import trees2; importlib.reload(trees2)
    assets = get_coll("TreeAssets")
    V = trees2.make_trees(assets)
    bpy.context.view_layer.layer_collection.children["TreeAssets"].exclude = True
    return V

def tree_assets_v1():
    assets = get_coll("TreeAssets"); rnd = random.Random(3); variants = {}
    def make(name, kind, color):
        bm = bmesh.new()
        h = {"camphor": 2.6, "ginkgo": 2.8, "sequoia": 1.5, "willow": 3.0, "shrub": 0.2}[kind]
        bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=0.25, radius2=0.18, depth=h, matrix=Matrix.Translation((0, 0, h / 2)))
        nt = len(bm.faces)
        if kind == "camphor":
            # 香樟：树干分叉，树冠宽而浓密、略扁，由一圈团簇组成，外轮廓不规则
            for ang, lean in ((0.3, 0.55), (2.4, 0.5), (4.3, 0.6)):
                d = Vector((math.cos(ang) * lean, math.sin(ang) * lean, 1)).normalized()
                bmesh.ops.create_cone(bm, cap_ends=True, segments=5, radius1=0.17, radius2=0.09, depth=2.0,
                                      matrix=Matrix.Translation(Vector((0, 0, h)) + d * 1.0) @ d.to_track_quat("Z", "Y").to_matrix().to_4x4())
            nt = len(bm.faces)
            clumps = [(0, 0, 5.4, 2.4, 0.72)]
            for i in range(7):
                a_ = i / 7 * 2 * math.pi + rnd.uniform(-0.3, 0.3); rr = rnd.uniform(1.9, 2.5)
                clumps.append((math.cos(a_) * rr, math.sin(a_) * rr, rnd.uniform(4.3, 5.0), rnd.uniform(1.4, 1.9), 0.75))
            for i in range(3):
                a_ = rnd.uniform(0, 2 * math.pi); clumps.append((math.cos(a_) * 0.9, math.sin(a_) * 0.9, 6.2, rnd.uniform(1.2, 1.5), 0.8))
            for cx, cy, cz, r, sz in clumps:
                bmesh.ops.create_icosphere(bm, subdivisions=2, radius=r, matrix=Matrix.Translation((cx, cy, cz)) @ Matrix.Diagonal((1, 1, sz, 1)))
        elif kind == "ginkgo":
            bmesh.ops.create_icosphere(bm, subdivisions=2, radius=2.2, matrix=Matrix.Translation((0, 0, 5.5)) @ Matrix.Diagonal((0.85, 0.85, 1.45, 1)))
        elif kind == "sequoia":
            bmesh.ops.create_cone(bm, cap_ends=True, segments=8, radius1=1.35, radius2=0.05, depth=12, matrix=Matrix.Translation((0, 0, 7.6)))
        elif kind == "willow":
            # 垂柳：树干略斜，冠顶圆，四周垂下长短不一的细长枝条（像帘子）
            bmesh.ops.create_cone(bm, cap_ends=True, segments=5, radius1=0.15, radius2=0.08, depth=1.8,
                                  matrix=Matrix.Translation((0.5, 0.2, h + 0.6)) @ Matrix.Rotation(0.5, 4, 'Y'))
            bmesh.ops.create_cone(bm, cap_ends=True, segments=5, radius1=0.15, radius2=0.08, depth=1.8,
                                  matrix=Matrix.Translation((-0.4, -0.3, h + 0.6)) @ Matrix.Rotation(-0.45, 4, 'Y'))
            nt = len(bm.faces)
            bmesh.ops.create_icosphere(bm, subdivisions=2, radius=2.2, matrix=Matrix.Translation((0, 0, 6.0)) @ Matrix.Diagonal((1.2, 1.2, 0.75, 1)))
            # 垂枝：三圈，越外圈越长，枝条宽而互相重叠，形成“帘子”
            for ring_r, n_, top, Lr, wr in ((2.55, 40, 6.0, (3.6, 5.0), (0.55, 0.75)),
                                            (1.9, 28, 6.6, (3.4, 4.6), (0.5, 0.7)),
                                            (1.1, 16, 7.0, (2.6, 3.6), (0.45, 0.6))):
                for i in range(n_):
                    a_ = i / n_ * 2 * math.pi + rnd.uniform(-0.1, 0.1)
                    L_ = rnd.uniform(*Lr)
                    x_, y_ = math.cos(a_) * ring_r, math.sin(a_) * ring_r
                    m_ = Matrix.Translation((x_ * 1.06, y_ * 1.06, top - L_ / 2)) @ Matrix.Rotation(a_, 4, 'Z') @ Matrix.Rotation(-0.12, 4, 'Y')
                    bmesh.ops.create_cone(bm, cap_ends=True, segments=4, radius1=0.04, radius2=rnd.uniform(*wr), depth=L_, matrix=m_ @ Matrix.Diagonal((0.55, 1.0, 1, 1)))
        else:
            bmesh.ops.create_icosphere(bm, subdivisions=1, radius=0.9, matrix=Matrix.Translation((0, 0, 0.7)) @ Matrix.Diagonal((1.2, 1.2, 0.8, 1)))
        crown = [v for f in list(bm.faces)[nt:] for v in f.verts]
        for v in set(crown): v.co += Vector((rnd.uniform(-.18, .18), rnd.uniform(-.18, .18), rnd.uniform(-.18, .18)))
        me = bpy.data.meshes.new(name); bm.to_mesh(me); bm.free()
        me.materials.append(mat("trunk")); me.materials.append(mat(color))
        for i, p in enumerate(me.polygons): p.material_index = 0 if i < nt else 1
        ob = bpy.data.objects.new(name, me); assets.objects.link(ob)
        c = bpy.data.collections.new(name + "_C"); c.objects.link(ob); variants[name] = c
    make("Camphor", "camphor", "camphor"); make("Camphor2", "camphor", "camphor2")
    make("Ginkgo", "ginkgo", "ginkgo"); make("Sequoia", "sequoia", "sequoia")
    make("Willow", "willow", "willow"); make("Shrub", "shrub", "shrub")
    bpy.context.view_layer.layer_collection.children["TreeAssets"].exclude = True
    return variants

def build_trees():
    """按新卫星图分区种树：密林带 + 行道树列 + 零星点缀；湖周开阔草坪不再随机铺满"""
    V = tree_assets(); trees = get_coll("Trees", get_coll("Site")); rnd = random.Random(SEED)
    from shapely.geometry import Point, Polygon, LineString
    from shapely.prepared import prep
    sunken = prep(SITE["G"].buffer(1.2)); libp = Path2(Perimeter(SITE["lib_ring"]).offset(6))
    blds = [Path2([px2m(u0, v1), px2m(u1, v1), px2m(u1, v0), px2m(u0, v0)]) for _, (u0, v0, u1, v1), _ in NEIGHBORS]
    blds.append(Path2(Perimeter(rounded_polygon(SOUTH_BLDG[0], SOUTH_BLDG[1])).offset(5)))
    plazas = [Path2(pxs(p)) for _, p in PLAZAS] + [Path2(p) for _, p, _ in PLAZAS_M]
    lines = [(l, w) for l, w in SITE["roads"]] + [(l, w) for l, w in SITE["paths"]]
    placed = []; grid = {}
    # ---- 遮挡判断：相机从西北看向东南，一棵树会挡住它东南方向一段地面
    from shapely.ops import unary_union as _uu
    from shapely.affinity import translate as _tr
    blds_poly = [Polygon([px2m(u0, v1), px2m(u1, v1), px2m(u1, v0), px2m(u0, v0)]) for _, (u0, v0, u1, v1), _ in NEIGHBORS]
    blds_poly.append(Polygon(rounded_polygon(SOUTH_BLDG[0], SOUTH_BLDG[1])))
    blds_poly.append(Polygon(rounded_polygon(LIB_FOOT, LIB_RADII)))
    KEY = _uu([SITE["road_union"], SITE["sidewalk"], SITE["S0"]] + [b.buffer(0.5) for b in blds_poly] +
              [LineString(l).buffer(w / 2) for l, w in SITE["paths"]]).buffer(0)
    KEYp = prep(KEY)
    TAN = math.tan(math.radians(57))
    VD = np.array([0.539, -0.643]); VD /= np.linalg.norm(VD)          # 视线的水平方向（指向东南）
    def occluded_area(x, y, v, sc):
        top, r = TREE_DIMS[v]; top *= sc; r *= sc
        L = (top - r * 0.6) * TAN
        shadow = LineString([(x, y), (x + VD[0] * L, y + VD[1] * L)]).buffer(r)
        if not KEYp.intersects(shadow): return 0.0
        return shadow.intersection(KEY).area
    def fit_scale(x, y, v, s0):
        """所有树保持同样大小：遮挡关键区域过多就去掉，不再缩小"""
        return s0 if occluded_area(x, y, v, s0) <= OCCLUDE_MAX else None
    stats = {"shrunk": 0, "dropped": 0}
    def ok(x, y, clear=1.5):
        if sunken.contains(Point(x, y)) or libp.contains(x, y) or any(b.contains(x, y) for b in blds) or any(p.contains(x, y) for p in plazas): return False
        for line, w in lines:
            if dist_to_line(x, y, line) < w / 2 + clear + (1.8 if w >= 6 else 0): return False
        for bx, by, *_ in SITE["benches"]:
            if math.hypot(x - bx, y - by) < 4: return False
        return True
    def free(x, y, d):
        gx, gy = int(x // 6), int(y // 6)
        return not any((x - a) ** 2 + (y - b) ** 2 < d * d for i in (-1, 0, 1) for j in (-1, 0, 1) for a, b in grid.get((gx + i, gy + j), []))
    def place(x, y, v, s=None, rot=None, keep=False):
        s0 = (s or rnd.uniform(0.85, 1.2)) * TREE_SCALE
        sc = s0 if keep else fit_scale(x, y, v, s0)
        if sc is None:
            stats["dropped"] += 1; return
        if sc < s0 - 1e-6: stats["shrunk"] += 1
        s = sc / TREE_SCALE
        grid.setdefault((int(x // 6), int(y // 6)), []).append((x, y))
        e = bpy.data.objects.new("Tree", None); e.instance_type = "COLLECTION"; e.instance_collection = V[v]
        e.location = (x, y, 0); e.rotation_euler = (0, 0, rot if rot is not None else rnd.uniform(0, 6.28))
        sc = s * TREE_SCALE; e.scale = (sc, sc, sc); trees.objects.link(e); placed.append((x, y))
    def row(pts, step, kind, s=(0.8, 1.0)):
        for x, y, a in resample(pts + [pts[-1]], step):
            if ok(x, y, 0.6) and free(x, y, step * 0.8): place(x, y, kind if isinstance(kind, str) else rnd.choice(kind), rnd.uniform(*s))
    # 1) 湖东侧道路：两排香樟（卫星图量取：西排间距 6.4 m、东排 5.5 m，各距路中心 4.4 m）
    east_line = [l for (n_, _, _), (l, w) in zip(road_list(), SITE["roads"]) if n_ == "Road_LakeEast"][0]
    a_, b_ = np.array(east_line[0]), np.array(east_line[-1]); d_ = (b_ - a_) / np.linalg.norm(b_ - a_); nn_ = np.array([-d_[1], d_[0]])   # nn_ 指向东
    for off, step, s0, s1 in ((-5.2, 14.0, 0, 128), (5.2, 14.0, 7, 84)):
        for k, t in enumerate(np.arange(s0 + 2, s1, step)):
            q = a_ + d_ * t + nn_ * off
            if any(dist_to_line(q[0], q[1], l) < w / 2 + 1.2 for l, w in SITE["paths"]): continue
            place(q[0], q[1], "Camphor" if k % 2 == 0 else "Camphor2", 1.0, rot=k * 1.3, keep=True)
    # 湖东南角草地：3 棵香樟（卫星图）
    for x, y in ((9.1, 51.0), (16.1, 55.1), (17.8, 47.9)): place(x, y, "Camphor2", 0.9)
    # 湖南岸靠图书馆：4 棵垂柳，等距排在挡墙 / 台阶后面的草地上
    lpw = Polygon(SITE["lake"]).buffer(RING_W + STEP_D * STEP_N + 2.2, join_style=1).exterior
    cand = [lpw.interpolate(d) for d in np.arange(0, lpw.length, 0.5)]
    n_arch = LIB_ARCHES[0][0]
    ne_line = [(-23.5, 4.6), (-23.5, 30)]
    def clear_of_walks(x, y):
        return dist_to_line(x, y, ne_line) > 4.5 and all(dist_to_line(x, y, l) > w / 2 + 2.5 for l, w in SITE["paths"])
    cand = [p for p in cand if -40 < p.x < -2 and p.y < 34 and clear_of_walks(p.x, p.y)]
    if cand:
        # 等距挑 4 个点（彼此至少相隔 7 m）
        cand.sort(key=lambda p: p.x); picked = []
        for t in np.linspace(0, 1, 4):
            target = cand[0].x + (cand[-1].x - cand[0].x) * t
            p = min(cand, key=lambda q: abs(q.x - target))
            if all(math.hypot(p.x - q.x, p.y - q.y) > 7 for q in picked): picked.append(p)
        for k, p in enumerate(picked): place(p.x, p.y, "Willow", 0.95, rot=k * 2.1)
    # 2) 其他道路两侧水杉
    for (n_, _, _), (line, w) in zip(road_list(), SITE["roads"]):
        if n_ == "Road_LakeEast": continue
        for side in (-1, 1):
            row(offset_line(line, side * (w / 2 + 4.2)), 14.0, "Sequoia", (0.95, 1.0))
    row([(-74.5, 6), (-74.5, -46)], 6.0, "Ginkgo", (0.7, 0.85)); row([(-67.5, 6), (-67.5, -46)], 6.0, "Ginkgo", (0.7, 0.85))
    row([(-125, 12.0), (-82, 12.0)], 6.0, "Shrub", (1.1, 1.3))
    lp = Perimeter(SITE["lake"])
    for u in np.arange(0, lp.total, 6.5):
        p, n = lp.at(u); q = p + n * (RING_W + STEP_D * STEP_N + 2.0)
        pass   # 湖岸垂柳改为按卫星图位置单独布置
    # 3) 密林带：西侧龙城路林带、湖北岸、东北角林地
    woods = [((-262, -120), (-215, 240), 5.4), ((-215, 186), (-10, 240), 5.8), ((8, 128), (75, 240), 5.8)]
    for (x0, y0), (x1, y1), d in woods:
        for _ in range(int((x1 - x0) * (y1 - y0) / (d * d) * 1.6)):
            x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
            if ok(x, y) and free(x, y, d): place(x, y, rnd.choice(["Camphor", "Camphor2", "Camphor", "Ginkgo"]))
    # 4) 卫星图范围以外（东侧、南侧）：中等密度
    for _ in range(9000):
        x, y = rnd.uniform(SCENE_X[0] - 90, SCENE_X[1] + 90), rnd.uniform(SCENE_Y[0] - 90, SCENE_Y[1] + 90)
        inside_sat = -215 < x < 60 and -60 < y < 240
        if inside_sat or not ok(x, y) or not free(x, y, 9.0): continue
        r = rnd.random(); place(x, y, "Camphor" if r < .45 else "Camphor2" if r < .7 else "Ginkgo" if r < .85 else "Shrub")
    # 5) 开阔草坪上零星几棵（卫星图上可见）
    for x, y in ((-96, -4), (-92, -8), (-99, -9), (-160, -25)):
        if ok(x, y, 0.5): place(x, y, "Camphor2", 0.9)
    # 6) 图书馆南广场两侧银杏树阵
    for i in range(6):
        for sx in (-34, 30):
            place(sx + (i % 3) * 5, -46 - (i // 3) * 5, "Ginkgo", 0.9, 0)
    print("TREES placed", len(placed), "shrunk", stats["shrunk"], "dropped", stats["dropped"])
    return len(placed)


# ---------------------------------------------------------------- 学生
def person_assets():
    assets = get_coll("PeopleAssets"); variants = []; rnd = random.Random(5)
    def part(bm, center, size, rot=None):
        m = Matrix.Translation(center)
        if rot: m = m @ rot
        bmesh.ops.create_cube(bm, size=1.0, matrix=m @ Matrix.Diagonal((*size, 1)))
    for k in range(12):
        shirt = SHIRTS[k % len(SHIRTS)]; pants = [COLORS["jeans"], COLORS["darkpants"], COLORS["khaki"]][k % 3]
        stride = math.radians(rnd.choice([18, 24, 28]))
        def pants_fn(bm):
            for side, s in ((0.09, 1), (-0.09, -1)):
                m = Matrix.Translation((side, 0, 0.88)) @ Matrix.Rotation(s * stride, 4, 'X') @ Matrix.Translation((0, 0, -0.44))
                bmesh.ops.create_cube(bm, size=1.0, matrix=m @ Matrix.Diagonal((0.14, 0.15, 0.88, 1)))
        def shirt_fn(bm):
            bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((0, 0, 1.17)) @ Matrix.Diagonal((0.40, 0.24, 0.60, 1)))
            for side, s in ((0.25, -1), (-0.25, 1)):
                m = Matrix.Translation((side, 0, 1.44)) @ Matrix.Rotation(s * stride * 0.8, 4, 'X') @ Matrix.Translation((0, 0, -0.30))
                bmesh.ops.create_cube(bm, size=1.0, matrix=m @ Matrix.Diagonal((0.10, 0.11, 0.60, 1)))
        def skin_fn(bm):
            bmesh.ops.create_icosphere(bm, subdivisions=1, radius=0.12, matrix=Matrix.Translation((0, 0.01, 1.62)))
        def hair_fn(bm):
            bmesh.ops.create_icosphere(bm, subdivisions=1, radius=0.13, matrix=Matrix.Translation((0, -0.02, 1.67)) @ Matrix.Diagonal((1, 1, 0.75, 1)))
        def bag_fn(bm):
            bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation((0, -0.2, 1.18)) @ Matrix.Diagonal((0.32, 0.14, 0.42, 1)))
        parts = [("pants", pants_fn, pants), ("shirt", shirt_fn, shirt), ("skin", skin_fn, COLORS["skin"]), ("hair", hair_fn, COLORS["hair"])]
        if k % 3 != 2: parts.append(("bag", bag_fn, SHIRTS[(k * 3 + 4) % len(SHIRTS)]))
        bm = bmesh.new(); me = bpy.data.meshes.new(f"Student_{k}"); mats = []
        for i, (pn, fn, col) in enumerate(parts):
            b2 = bmesh.new(); fn(b2); tmp = bpy.data.meshes.new("tmp"); b2.to_mesh(tmp); b2.free()
            for p in tmp.polygons: p.material_index = i
            bm.from_mesh(tmp); bpy.data.meshes.remove(tmp)
            mats.append(mat(f"p_{pn}_{k}", col, 0.75))
        bm.to_mesh(me); bm.free()
        for m in mats: me.materials.append(m)
        # bm.from_mesh 会把 material_index 带过来
        ob = bpy.data.objects.new(f"Student_{k}", me); assets.objects.link(ob); variants.append(me)
    bpy.context.view_layer.layer_collection.children["PeopleAssets"].exclude = True
    return variants

def build_lamps():
    """路灯：环湖步道与园路用 3.6 m 庭院灯，车行道两侧用 7 m 路灯。白天是道具；LAMPS_ON 时灯头发光并放置点光源"""
    from shapely.geometry import Point, Polygon, LineString
    from shapely.prepared import prep
    coll = get_coll("Lamps", get_coll("Site")); rnd = random.Random(SEED + 7)
    lake = Polygon(SITE["lake"]); sunken = prep(SITE["G"].buffer(0.4))
    blds = [Polygon([px2m(u0, v1), px2m(u1, v1), px2m(u1, v0), px2m(u0, v0)]) for _, (u0, v0, u1, v1), _ in NEIGHBORS]
    blds += [Polygon(rounded_polygon(SOUTH_BLDG[0], SOUTH_BLDG[1])), Polygon(rounded_polygon(LIB_FOOT, LIB_RADII))]
    blds = prep(unary_union_([b.buffer(1.5) for b in blds]))
    road = prep(SITE["road_union"].buffer(0.3))
    PV, PF, HV, HF = [], [], [], []; spots = []
    def box_(V, F, cx, cy, z0, sx, sy, sz, rot=0.0):
        b = len(V); c, s_ = math.cos(rot), math.sin(rot)
        for z in (z0, z0 + sz):
            for x, y in [(-sx / 2, -sy / 2), (sx / 2, -sy / 2), (sx / 2, sy / 2), (-sx / 2, sy / 2)]:
                V.append((cx + c * x - s_ * y, cy + s_ * x + c * y, z))
        F += [[b, b + 3, b + 2, b + 1], [b + 4, b + 5, b + 6, b + 7]] + [[b + i, b + (i + 1) % 4, b + 4 + (i + 1) % 4, b + 4 + i] for i in range(4)]
    placed = []
    def free(x, y, d=7.0): return all((x - a) ** 2 + (y - b) ** 2 > d * d for a, b in placed)
    def garden(x, y, z):
        placed.append((x, y)); box_(PV, PF, x, y, z, 0.11, 0.11, 3.3)
        box_(HV, HF, x, y, z + 3.3, 0.30, 0.30, 0.42); box_(PV, PF, x, y, z + 3.72, 0.40, 0.40, 0.06)
        spots.append(("g", (x, y, z + 3.5)))
    def street(x, y, ang):
        placed.append((x, y)); box_(PV, PF, x, y, 0, 0.16, 0.16, 7.0)
        hx, hy = x + math.cos(ang) * 1.3, y + math.sin(ang) * 1.3
        box_(PV, PF, (x + hx) / 2, (y + hy) / 2, 6.9, 1.4, 0.08, 0.08, ang)
        box_(HV, HF, hx, hy, 6.78, 0.62, 0.26, 0.14, ang)
        spots.append(("s", (hx, hy, 6.7)))
    # 环湖步道：靠陆地一侧，每 14 m
    prom = [(x, y) for x, y, z in SITE["walks"]["prom"]]
    for x, y, a in resample(prom, 14)[::1]:
        nx, ny = -math.sin(a), math.cos(a)
        if Point(x + nx, y + ny).distance(lake) < Point(x - nx, y - ny).distance(lake): nx, ny = -nx, -ny
        px_, py_ = x + nx * 1.35, y + ny * 1.35
        if free(px_, py_, 9) and not blds.contains(Point(px_, py_)): garden(px_, py_, RING_Z)
    # 园路：每 18 m，左右交替
    k = 0
    for name, pts in SITE["walks"].items():
        if not name.startswith(("path", "northEntry", "linear", "upperEast")): continue
        line = [(x, y) for x, y, z in pts]
        if len(line) < 2: continue
        for x, y, a in resample(line, 18)[1:]:
            sgn = 1 if k % 2 else -1; k += 1
            px_, py_ = x - math.sin(a) * 2.0 * sgn, y + math.cos(a) * 2.0 * sgn; p = Point(px_, py_)
            if free(px_, py_) and not sunken.contains(p) and not blds.contains(p) and not road.contains(p) and not lake.contains(p): garden(px_, py_, 0)
    # 车行道：两侧交错，每 28 m，灯臂伸向路中
    for line, w in SITE["roads"]:
        for side, off0 in ((-1, 0.0), (1, 14.0)):
            ol = offset_line(line, side * (w / 2 + 3.3))
            for i, (x, y, a) in enumerate(resample(ol, 28)):
                if i == 0 and off0: continue
                p = Point(x, y)
                if free(x, y, 10) and not sunken.contains(p) and not blds.contains(p) and not lake.contains(p):
                    street(x, y, a + (math.pi / 2 if side < 0 else -math.pi / 2))
    mesh_obj("LampPoles", PV, PF, mat("lamp_pole", (0.16, 0.17, 0.18), 0.45), coll)
    hm = bpy.data.materials.new("lamp_head"); hm.use_nodes = True; hb = hm.node_tree.nodes["Principled BSDF"]
    hb.inputs["Base Color"].default_value = (0.92, 0.90, 0.85, 1); hb.inputs["Roughness"].default_value = 0.3
    if LAMPS_ON:
        hb.inputs["Emission Color"].default_value = (1.0, 0.80, 0.55, 1); hb.inputs["Emission Strength"].default_value = 28.0 * ART
    mesh_obj("LampHeads", HV, HF, hm, coll)
    if LAMPS_ON:
        lg = bpy.data.lights.new("lamp_g", "POINT"); lg.energy = 130 * ART; lg.color = (1.0, 0.78, 0.50); lg.shadow_soft_size = 0.15
        ls = bpy.data.lights.new("lamp_s", "SPOT"); ls.energy = 600 * ART; ls.color = (1.0, 0.82, 0.58); ls.spot_size = math.radians(120); ls.spot_blend = 0.6; ls.shadow_soft_size = 0.2
        for kind, (x, y, z) in spots:
            ob = bpy.data.objects.new("LampLight", lg if kind == "g" else ls); coll.objects.link(ob); ob.location = (x, y, z - 0.05)
        # 图书馆入口：拱门内一盏暖光
        for (ax, ay), aw, ah in LIB_ARCHES[:2]:
            al = bpy.data.lights.new("arch_light", "AREA"); al.energy = 2200 * ART; al.color = (1.0, 0.80, 0.55); al.size = 6
            ob = bpy.data.objects.new("ArchLight", al); coll.objects.link(ob)
            ob.location = (ax, ay + (1.5 if ay > -10 else -1.5), 6.5); ob.rotation_euler = (0, 0, 0)
    SITE["lamps"] = [(round(x, 2), round(y, 2), round(z, 2), k_) for k_, (x, y, z) in spots]
    print("LAMPS", len(spots), "on" if LAMPS_ON else "off")

def unary_union_(geoms):
    from shapely.ops import unary_union
    return unary_union(geoms)

def time_of_day(sc, sky, mx, bg, sun_d, sun_ob):
    """按 TIME 设置太阳强度 / 颜色、天空、曝光；日落后关掉太阳、加月光与夜空"""
    el = SUN_EL; lerp = lambda a, b, t: tuple(a[i] + (b[i] - a[i]) * t for i in range(len(a)))
    sky.sun_elevation = math.radians(el)
    if el > 0:
        k = (math.sin(math.radians(el)) / math.sin(math.radians(28))) ** 0.5
        sun_d.energy = 4.6 * max(0.85, min(1.15, k))
        t = min(1.0, max(0.0, (el - 2) / 32)) ** 0.6
        sun_d.color = lerp((1.0, 0.44, 0.20), (1.0, 0.94, 0.86), t)
        mx.inputs["Factor"].default_value = 0.55 + 0.35 * (1 - t)          # 低太阳时天空光更多来自天空本身（偏暖的黄昏色）
        sc.view_settings.exposure = EXPO
        if el < 12: bg.inputs["Strength"].default_value = 0.10 * (1 + (12 - el) * 0.12)
    elif el > -12:                                                     # 黎明 / 黄昏（蓝调时刻）
        sun_d.energy = 0.0; mx.inputs["Factor"].default_value = 0.45          # 蓝调：天空色 + 深蓝环境光
        mx.inputs["B"].default_value = (0.06, 0.10, 0.24, 1)
        bg.inputs["Strength"].default_value = 0.10 * (1.6 + (-el) * 1.1)
        sc.view_settings.exposure = EXPO
    else:                                                              # 夜晚：深蓝夜空 + 很弱的月光
        for l in list(bg.inputs["Color"].links): sc.world.node_tree.links.remove(l)
        bg.inputs["Color"].default_value = (0.010, 0.016, 0.034, 1); bg.inputs["Strength"].default_value = 1.0
        sun_d.energy = 0.06; sun_d.color = (0.62, 0.72, 1.0); sun_d.angle = math.radians(0.5)
        sun_ob.rotation_euler = (-Vector((0.45, -0.55, 0.70)).normalized()).to_track_quat("-Z", "Y").to_euler()
        sc.view_settings.exposure = EXPO

def build_people():
    V = person_assets(); coll = get_coll("Students", get_coll("Site")); rnd = random.Random(SEED + 1); pts = []
    def put(x, y, heading):
        ob = bpy.data.objects.new("Student", rnd.choice(V)); coll.objects.link(ob)
        ob.location = (x, y, 0.05); ob.rotation_euler = (0, 0, heading - math.pi / 2)   # 模型面朝 +y
        s = PEOPLE_SCALE * rnd.uniform(0.92, 1.06); ob.scale = (s, s, s); pts.append((x, y))
    def along(line, every, jitter=1.0, closed=False):
        L = line + [line[0]] if closed else line
        for x, y, a in resample(L, 1.0):
            if rnd.random() < 1.0 / every:
                d = rnd.choice([0, math.pi]); off = rnd.uniform(-jitter, jitter)
                put(x - math.sin(a) * off, y + math.cos(a) * off, a + d)
                if rnd.random() < 0.35:     # 结伴同行
                    put(x - math.sin(a) * (off + 0.8), y + math.cos(a) * (off + 0.8) + 0.2, a + d)
    along([(x, y) for x, y, z in SITE["walks"]["prom"]], 14, 0.8)
    for k_, pts in SITE["walks"].items():
        if k_.startswith("path"): along([(x, y) for x, y, z in pts], 14, 0.5)
    n_arch = LIB_ARCHES[0][0]
    along([(n_arch[0], n_arch[1] + 4), (n_arch[0] - 2, 14), (n_arch[0] - 6, 26)], 4, 1.5)
    for line, w in SITE["roads"]:
        for side in (-1, 1):
            along(offset_line(line, side * (w / 2 + 1.4)), 20, 0.5)
    # 图书馆南、北入口前的人流
    for (cx, cy), n in (((-1.5, -40), 14), ((-6, 4), 9)):
        for i in range(n):
            put(cx + rnd.uniform(-9, 9), cy + rnd.uniform(-5, 5), rnd.choice([math.pi / 2, -math.pi / 2]) + rnd.uniform(-.6, .6))
    return len(pts)


# ---------------------------------------------------------------- 白鹭
def egret_assets():
    assets = get_coll("EgretAssets"); out = {}
    def mk(name, flying, wing_deg=0):
        parts = []
        def body(bm):
            bmesh.ops.create_icosphere(bm, subdivisions=2, radius=1.0, matrix=Matrix.Translation((0, 0, 0)) @ Matrix.Diagonal((0.12, 0.42, 0.12, 1)))
            if flying:
                # 飞行时颈部缩成 S 形，头靠近身体
                bmesh.ops.create_icosphere(bm, subdivisions=1, radius=1.0, matrix=Matrix.Translation((0, 0.42, 0.06)) @ Matrix.Diagonal((0.08, 0.12, 0.09, 1)))
                bmesh.ops.create_icosphere(bm, subdivisions=1, radius=0.07, matrix=Matrix.Translation((0, 0.55, 0.08)))
                for s in (-1, 1):
                    w = math.radians(wing_deg)
                    pts = [(0.05 * s, -0.12), (0.05 * s, 0.18), (0.55 * s, 0.14), (0.9 * s, 0.02), (0.85 * s, -0.10), (0.45 * s, -0.16)]
                    vs = [bm.verts.new((x, y, abs(x) * math.tan(w))) for x, y in pts]
                    bm.faces.new(vs if s > 0 else vs[::-1])
            else:
                for i, (y, z, r) in enumerate([(0.30, 0.18, 0.05), (0.33, 0.36, 0.045), (0.28, 0.52, 0.04)]):
                    bmesh.ops.create_cone(bm, cap_ends=True, segments=6, radius1=r, radius2=r * 0.9, depth=0.22,
                                          matrix=Matrix.Translation((0, y, z)) @ Matrix.Rotation(math.radians([-40, 15, -30][i]), 4, 'X'))
                bmesh.ops.create_icosphere(bm, subdivisions=1, radius=0.065, matrix=Matrix.Translation((0, 0.31, 0.64)))
        def bill(bm):
            if flying: m = Matrix.Translation((0, 0.68, 0.07)) @ Matrix.Rotation(math.radians(-90), 4, 'X')
            else: m = Matrix.Translation((0, 0.43, 0.63)) @ Matrix.Rotation(math.radians(-95), 4, 'X')
            bmesh.ops.create_cone(bm, cap_ends=True, segments=5, radius1=0.022, radius2=0.002, depth=0.2, matrix=m)
        def legs(bm):
            for s in (-1, 1):
                if flying: m = Matrix.Translation((0.03 * s, -0.62, -0.03)) @ Matrix.Rotation(math.radians(90), 4, 'X')
                else: m = Matrix.Translation((0.04 * s, 0.0, -0.42))
                bmesh.ops.create_cone(bm, cap_ends=True, segments=4, radius1=0.012, radius2=0.012, depth=0.6, matrix=m)
        me = bpy.data.meshes.new(name); bm = bmesh.new(); mats = []
        for i, (fn, c) in enumerate([(body, "white"), (bill, "yellow"), (legs, "black")]):
            b2 = bmesh.new(); fn(b2); tmp = bpy.data.meshes.new("tmp"); b2.to_mesh(tmp); b2.free()
            for p in tmp.polygons: p.material_index = i
            bm.from_mesh(tmp); bpy.data.meshes.remove(tmp); mats.append(mat(c, rough=0.55))
        bm.to_mesh(me); bm.free()
        for m in mats: me.materials.append(m)
        for p in me.polygons: p.use_smooth = False
        ob = bpy.data.objects.new(name, me); assets.objects.link(ob); out[name] = me
    mk("Egret_up", True, 28); mk("Egret_mid", True, 4); mk("Egret_down", True, -22); mk("Egret_stand", False)
    bpy.context.view_layer.layer_collection.children["EgretAssets"].exclude = True
    return out

def build_egrets():
    E = egret_assets(); coll = get_coll("Egrets", get_coll("Site")); rnd = random.Random(SEED + 2); pos = []
    # 一小群白鹭从湖面向东南飞过图书馆上空
    flock = [(-70, 95, 26), (-58, 88, 30), (-80, 82, 22), (-46, 76, 34), (-64, 70, 28), (-30, 55, 38), (-95, 120, 18),
             (-130, 70, 15), (-20, 30, 44)]
    for i, (x, y, z) in enumerate(flock):
        ob = bpy.data.objects.new("Egret", E[["Egret_up", "Egret_mid", "Egret_down"][i % 3]]); coll.objects.link(ob)
        ob.location = (x, y, z); heading = math.radians(-35 + rnd.uniform(-12, 12))
        ob.rotation_euler = (math.radians(rnd.uniform(-6, 6)), math.radians(rnd.uniform(-10, 10)), heading - math.pi / 2 + math.pi)
        s = EGRET_SCALE * rnd.uniform(0.9, 1.1); ob.scale = (s, s, s); pos.append((x, y, z))
    # 湖边浅水处站立的白鹭
    pp = Perimeter(SITE["lake"])
    for u in (40, 52, 230, 470, 480):
        p, n = pp.at(u); q = p - n * 1.2
        ob = bpy.data.objects.new("EgretStand", E["Egret_stand"]); coll.objects.link(ob)
        ob.location = (q[0], q[1], 0.5 * EGRET_SCALE * 0.9); ob.rotation_euler = (0, 0, rnd.uniform(0, 6.28))
        ob.scale = (EGRET_SCALE * 0.9,) * 3
    return pos


# ---------------------------------------------------------------- 车辆
def build_cars():
    """（已停用）这里不是停车场：不放车辆。保留函数以便以后需要时恢复"""
    return
    coll = get_coll("Cars", get_coll("Site")); rnd = random.Random(SEED + 3)
    me_list = []
    for k, c in enumerate(["car1", "car2", "car3", "car4", "car1", "car2"]):
        bm = bmesh.new()
        bmesh.ops.create_cube(bm, size=1, matrix=Matrix.Translation((0, 0, 0.55)) @ Matrix.Diagonal((1.8, 4.4, 0.7, 1)))
        bmesh.ops.create_cube(bm, size=1, matrix=Matrix.Translation((0, -0.2, 1.15)) @ Matrix.Diagonal((1.6, 2.3, 0.55, 1)))
        me = bpy.data.meshes.new(f"Car{k}"); bm.to_mesh(me); bm.free(); me.materials.append(mat(c, rough=0.3, metal=0.3)); me_list.append(me)
    u0, v0, u1, v1 = 498, 456, 536, 542
    a = px2m(u0, v1); b = px2m(u1, v0)
    for row in range(2):
        x = a[0] + 6 + row * 18
        for y in np.arange(a[1] + 3, b[1] - 3, 2.8):
            if rnd.random() < 0.7:
                ob = bpy.data.objects.new("Car", rnd.choice(me_list)); coll.objects.link(ob)
                ob.location = (x, y, 0.05); ob.rotation_euler = (0, 0, math.pi / 2 + rnd.choice([0, math.pi]))
    box("ParkingSurface", (a[0] + b[0]) / 2, (a[1] + b[1]) / 2, 0.0, b[0] - a[0], b[1] - a[1], 0.05, mat("asphalt"), coll)
    for line, w in SITE["roads"]:
        for x, y, ang in resample(line, 1.0):
            if rnd.random() < 0.012:
                ob = bpy.data.objects.new("Car", rnd.choice(me_list)); coll.objects.link(ob)
                side = rnd.choice([-1, 1]); ox, oy = -math.sin(ang) * side * w * 0.22, math.cos(ang) * side * w * 0.22
                ob.location = (x + ox, y + oy, 0.08); ob.rotation_euler = (0, 0, ang - math.pi / 2 + (math.pi if side < 0 else 0))


# ---------------------------------------------------------------- 相机 / 灯光
def setup_render(w, h, samples):
    sc = bpy.context.scene
    cd = bpy.data.cameras.new("IsoCam"); cd.type = "ORTHO"
    cam = bpy.data.objects.new("IsoCam", cd); sc.collection.objects.link(cam); sc.camera = cam
    cam.rotation_euler = (math.radians(57), 0, math.radians(-140))     # 从西北隔湖看向东南（临湖立面）
    R = cam.rotation_euler.to_matrix(); right = R @ Vector((1, 0, 0)); up = R @ Vector((0, 1, 0)); view = R @ Vector((0, 0, -1))
    target = Vector(CAM_TARGET)
    cd.ortho_scale = CAM_WIDTH
    cam.location = target - view * 600; cd.clip_end = 2000
    sun_d = bpy.data.lights.new("Sun", "SUN"); sun_d.energy = 3.4; sun_d.angle = math.radians(2.5)
    sun_d.color = (1.0, 0.95, 0.86)
    sun = bpy.data.objects.new("Sun", sun_d); sc.collection.objects.link(sun)
    src = Vector(SUN_SRC).normalized()     # 秋日午后，西南偏西
    sun.rotation_euler = (-src).to_track_quat("-Z", "Y").to_euler()
    world = bpy.data.worlds.new("World"); sc.world = world; world.use_nodes = True
    bg = world.node_tree.nodes["Background"]; bg.inputs[0].default_value = (0.72, 0.82, 1.0, 1); bg.inputs[1].default_value = 0.75
    sc.render.engine = "CYCLES"; sc.cycles.device = "CPU"; sc.cycles.samples = samples
    use_gpu(sc)
    sc.cycles.use_denoising = True; sc.cycles.max_bounces = 4
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = w, h, 100
    sc.view_settings.view_transform = "AgX"; sc.view_settings.look = "AgX - Punchy"; sc.view_settings.exposure = 0.25
    sc.render.image_settings.file_format = "PNG"
    if HQ: upgrade_look(sc, world, sun_d, src, sun)
    return cam


def use_gpu(sc):
    """GPU=1 或在 Mac 上：用 Metal（Apple 芯片）/ OptiX / CUDA 渲染；失败则回落到 CPU"""
    want = os.environ.get("GPU", "1" if sys.platform == "darwin" else "0")
    if want == "0": return
    try:
        pr = bpy.context.preferences.addons["cycles"].preferences
        for kind in ("METAL", "OPTIX", "CUDA", "HIP", "ONEAPI"):
            try: pr.compute_device_type = kind
            except Exception: continue
            pr.get_devices(); devs = [d for d in pr.devices if d.type == kind]
            if devs:
                for d in pr.devices: d.use = d.type == kind
                sc.cycles.device = "GPU"; print("GPU:", kind, [d.name for d in devs]); break
        sc.cycles.use_auto_tile = True; sc.cycles.tile_size = 2048
    except Exception as e: print("GPU unavailable, using CPU:", e)


def upgrade_look(sc, world, sun_d, src, sun_ob=None):
    """升级版：物理天空 + 暖色斜阳 + 材质细节（砖缝、草地斑驳、沥青颗粒、水面波纹、墙面质感）"""
    # ---- 天空：多次散射物理天空，只作环境光（太阳仍用平行光，便于控制阴影）
    nt = world.node_tree; N = nt.nodes; Lk = nt.links
    bg = N["Background"]; sky = N.new("ShaderNodeTexSky"); sky.sky_type = "MULTIPLE_SCATTERING"
    sky.sun_disc = False; sky.sun_elevation = math.asin(src.z); sky.sun_rotation = math.atan2(src.y, src.x) + math.pi / 2
    sky.altitude = 50; sky.air_density = 1.2; sky.aerosol_density = 1.5
    # 天空光与中性灰混合，避免整体偏蓝
    mx = N.new("ShaderNodeMix"); mx.data_type = "RGBA"; mx.inputs["Factor"].default_value = 0.55
    Lk.new(sky.outputs["Color"], mx.inputs["A"]); mx.inputs["B"].default_value = (0.03, 0.03, 0.03, 1)
    Lk.new(mx.outputs["Result"], bg.inputs["Color"]); bg.inputs["Strength"].default_value = 0.10
    sun_d.energy = 4.6; sun_d.color = (1.0, 0.88, 0.72); sun_d.angle = math.radians(1.5)
    sc.cycles.max_bounces = 6; sc.cycles.diffuse_bounces = 4; sc.view_settings.exposure = 0.3
    sc.view_settings.look = "AgX - Medium High Contrast"
    if TIME: time_of_day(sc, sky, mx, bg, sun_d, sun_ob)

    def chain(m):
        nt = m.node_tree; b = nt.nodes.get("Principled BSDF"); return nt, b
    def base_link(nt, b):
        l = [x for x in nt.links if x.to_socket == b.inputs["Base Color"]]
        return l[0].from_socket if l else None
    def add_bump(nt, b, height_socket, strength, dist=0.1):
        bp = nt.nodes.new("ShaderNodeBump"); bp.inputs["Strength"].default_value = strength; bp.inputs["Distance"].default_value = dist
        nt.links.new(height_socket, bp.inputs["Height"])
        old = [x for x in nt.links if x.to_socket == b.inputs["Normal"]]
        if old: nt.links.new(old[0].from_socket, bp.inputs["Normal"])
        nt.links.new(bp.outputs["Normal"], b.inputs["Normal"])
    def mult_color(nt, b, fac_socket, color=(0.55, 0.53, 0.5)):
        src_ = base_link(nt, b)
        mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"
        nt.links.new(fac_socket, mix.inputs["Factor"])
        if src_: nt.links.new(src_, mix.inputs["A"])
        else: mix.inputs["A"].default_value = b.inputs["Base Color"].default_value
        mix.inputs["B"].default_value = (*color, 1); nt.links.new(mix.outputs["Result"], b.inputs["Base Color"])
    def tex_coord(nt):
        tc = nt.nodes.new("ShaderNodeTexCoord"); return tc.outputs["Object"]
    for m in list(bpy.data.materials):
        if not m.use_nodes or "Principled BSDF" not in m.node_tree.nodes: continue
        nt, b = chain(m); n = m.name
        if n.startswith("grass") or n.startswith("leaf_shrub"):
            # 草地：多尺度斑驳 + 细小起伏
            co = tex_coord(nt)
            nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 2.2; nz.inputs["Detail"].default_value = 10; nz.inputs["Roughness"].default_value = 0.6
            nt.links.new(co, nz.inputs["Vector"])
            ramp = nt.nodes.new("ShaderNodeValToRGB"); e = ramp.color_ramp.elements
            e[0].position = 0.3; e[0].color = (0.76, 0.79, 0.68, 1); e[1].position = 0.7; e[1].color = (1.04, 1.04, 1.0, 1)
            nt.links.new(nz.outputs["Fac"], ramp.inputs["Fac"])
            src_ = base_link(nt, b); mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            if src_: nt.links.new(src_, mix.inputs["A"])
            nt.links.new(ramp.outputs["Color"], mix.inputs["B"]); nt.links.new(mix.outputs["Result"], b.inputs["Base Color"])
            nz2 = nt.nodes.new("ShaderNodeTexNoise"); nz2.inputs["Scale"].default_value = 18; nt.links.new(co, nz2.inputs["Vector"])
            add_bump(nt, b, nz2.outputs["Fac"], 0.2, 0.03)
        elif n.split(".")[0] in ("pav", "ringp", "pathg", "pinkp", "arcp", "courtp", "paving"):
            # 铺装：砖缝（变暗的细线）+ 轻微凹凸
            co = tex_coord(nt)
            br = nt.nodes.new("ShaderNodeTexBrick"); br.inputs["Scale"].default_value = 0.6 if n.startswith(("pinkp", "pav")) else 0.45
            br.inputs["Mortar Size"].default_value = 0.015; br.inputs["Color1"].default_value = (1, 1, 1, 1); br.inputs["Color2"].default_value = (0.96, 0.96, 0.95, 1)
            br.inputs["Mortar"].default_value = (0.8, 0.79, 0.77, 1); br.offset = 0.5
            nt.links.new(co, br.inputs["Vector"])
            inv = nt.nodes.new("ShaderNodeMath"); inv.operation = "SUBTRACT"; inv.inputs[0].default_value = 1.0
            nt.links.new(br.outputs["Fac"], inv.inputs[1])
            src_ = base_link(nt, b); mix = nt.nodes.new("ShaderNodeMix"); mix.data_type = "RGBA"; mix.blend_type = "MULTIPLY"
            mix.inputs["Factor"].default_value = 1.0
            if src_: nt.links.new(src_, mix.inputs["A"])
            else: mix.inputs["A"].default_value = b.inputs["Base Color"].default_value
            nt.links.new(br.outputs["Color"], mix.inputs["B"]); nt.links.new(mix.outputs["Result"], b.inputs["Base Color"])
            add_bump(nt, b, br.outputs["Fac"], 0.08, 0.01)
        elif n.startswith(("asph", "asphalt")):
            co = tex_coord(nt)
            nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 60; nz.inputs["Detail"].default_value = 4
            nt.links.new(co, nz.inputs["Vector"]); add_bump(nt, b, nz.outputs["Fac"], 0.2, 0.01)
            b.inputs["Roughness"].default_value = 0.65
        elif n in ("cream", "band", "s_wall", "s_band", "nb_facade"):
            co = tex_coord(nt)
            nz = nt.nodes.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 6; nz.inputs["Detail"].default_value = 6
            nt.links.new(co, nz.inputs["Vector"]); add_bump(nt, b, nz.outputs["Fac"], 0.06, 0.02)
            b.inputs["Roughness"].default_value = 0.7
        elif n == "water":
            # 水面：大波 + 细纹两层，更低粗糙度，带一点深色
            co = tex_coord(nt)
            mp = nt.nodes.new("ShaderNodeMapping"); mp.inputs["Scale"].default_value = (1.0, 3.0, 1.0); nt.links.new(co, mp.inputs["Vector"])
            w1 = nt.nodes.new("ShaderNodeTexNoise"); w1.inputs["Scale"].default_value = 0.35; w1.inputs["Detail"].default_value = 3
            w2 = nt.nodes.new("ShaderNodeTexNoise"); w2.inputs["Scale"].default_value = 4.0; w2.inputs["Detail"].default_value = 6
            nt.links.new(mp.outputs["Vector"], w1.inputs["Vector"]); nt.links.new(mp.outputs["Vector"], w2.inputs["Vector"])
            add_ = nt.nodes.new("ShaderNodeMath"); add_.operation = "MULTIPLY_ADD"; add_.inputs[1].default_value = 0.35
            nt.links.new(w2.outputs["Fac"], add_.inputs[0]); nt.links.new(w1.outputs["Fac"], add_.inputs[2])
            for l in list(nt.links):
                if l.to_socket == b.inputs["Normal"]: nt.links.remove(l)
            add_bump(nt, b, add_.outputs[0], 0.035, 0.05)
            b.inputs["Roughness"].default_value = 0.02; b.inputs["Base Color"].default_value = (0.035, 0.11, 0.12, 1)

def mask_mode():
    def emit(name, col):
        m = bpy.data.materials.new(name); m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
        e = nt.nodes.new("ShaderNodeEmission"); e.inputs[0].default_value = (*col, 1)
        o = nt.nodes.new("ShaderNodeOutputMaterial"); nt.links.new(e.outputs[0], o.inputs[0]); return m
    red, green, black = emit("mR", (1, 0, 0)), emit("mG", (0, 1, 0)), emit("mK", (0, 0, 0))
    libobs = set(o.name for o in bpy.data.collections["Library"].all_objects)
    for ob in bpy.data.objects:
        if ob.type != "MESH": continue
        m = red if ob.name in libobs else green if ob.name == "Lake" else black
        ob.data.materials.clear(); ob.data.materials.append(m)
        for p in ob.data.polygons: p.material_index = 0
    for c in ("Trees", "Students", "Egrets", "Cars", "Benches"):
        if c in bpy.data.collections: bpy.data.collections[c].hide_render = True
    sc = bpy.context.scene; sc.world.node_tree.nodes["Background"].inputs[1].default_value = 0
    sc.cycles.samples = 1; sc.cycles.use_denoising = False; sc.cycles.filter_width = 0.01
    sc.view_settings.view_transform = "Standard"; sc.view_settings.look = "None"

def project(p):
    from bpy_extras.object_utils import world_to_camera_view
    sc = bpy.context.scene; bpy.context.view_layer.update()
    co = world_to_camera_view(sc, sc.camera, Vector(p))
    return [co.x * sc.render.resolution_x, (1 - co.y) * sc.render.resolution_y]

def build_all():
    per = build_library(); build_neighbors(); build_site(per)
    nt = build_trees(); build_lamps()
    if NO_AGENTS: npl, eg = 0, []
    elif NO_EGRETS: npl = build_people(); eg = []
    else: npl = build_people(); eg = build_egrets()
    build_cars()
    return per, nt, npl, eg

def anim_data():
    """导出给网页动画用的数据：世界坐标→图像像素的投影、太阳方向、步道、湖岸"""
    p0 = project((0, 0, 0)); ex = project((1, 0, 0)); ey = project((0, 1, 0)); ez = project((0, 0, 1))
    proj = [p0, [ex[0] - p0[0], ex[1] - p0[1]], [ey[0] - p0[0], ey[1] - p0[1]], [ez[0] - p0[0], ez[1] - p0[1]]]
    r2 = lambda pts: [[round(x, 2), round(y, 2)] for x, y in pts]
    walks = {k: [list(p) for p in v] for k, v in SITE["walks"].items()}
    n_arch = LIB_ARCHES[0][0]
    from shapely.geometry import LineString
    k = 0
    for (rname, _, _), (line, w) in zip(road_list(), SITE["roads"]):
        if rname == "Road_LakeEast": continue
        for side in (-1, 1):
            pts = LineString(offset_line(line, side * (w / 2 + 1.4))).difference(SITE["G"].buffer(1.5))
            for seg in ([pts] if pts.geom_type == "LineString" else list(getattr(pts, "geoms", []))):
                if seg.is_empty or seg.length < 3: continue
                walks[f"side{k}"] = [[round(x, 2), round(y, 2), 0.0] for x, y in seg.coords]; k += 1
    return {"proj": proj, "sun": list(Vector(SUN_SRC).normalized()), "walks": walks,
            "lake": r2(SITE["lake"]), "lamps": SITE.get("lamps", []), "plazaNorth": [n_arch[0], n_arch[1] + 6],
            "scale": {"people": PEOPLE_SCALE, "egret": EGRET_SCALE}, "waterZ": WATER_Z, "ringZ": RING_Z,
            "benches": SITE["benches"], "terraceSeats": SITE["terr_seats"],
            "door": [n_arch[0], n_arch[1] - 1.0], "doorOut": [n_arch[0], n_arch[1] + 4],
            "pinkPlaza": [-66.5, 20]}

def vis_mode():
    """地面可见性图：地面类表面 = 白，建筑 / 树 / 车 = 黑。网页据此判断学生是否被遮挡"""
    def emit(name, col):
        m = bpy.data.materials.new(name); m.use_nodes = True; nt = m.node_tree; nt.nodes.clear()
        e = nt.nodes.new("ShaderNodeEmission"); e.inputs[0].default_value = (*col, 1)
        o = nt.nodes.new("ShaderNodeOutputMaterial"); nt.links.new(e.outputs[0], o.inputs[0]); return m
    white, black = emit("vW", (1, 1, 1)), emit("vK", (0, 0, 0))
    ground = set(o.name for o in bpy.data.collections["Roads"].all_objects)
    ground |= {"Ground", "Lake", "LakeBed", "ParkingSurface"}
    for ob in bpy.data.objects:
        if ob.type != "MESH": continue
        g = ob.name in ground or ob.name.startswith(("Plaza", "Walk_", "Parking", "G_", "LibApron"))
        ob.data.materials.clear(); ob.data.materials.append(white if g else black)
        for p in ob.data.polygons: p.material_index = 0
    sc = bpy.context.scene; sc.world.node_tree.nodes["Background"].inputs[1].default_value = 0
    sc.cycles.samples = 1; sc.cycles.use_denoising = False; sc.cycles.filter_width = 0.01
    sc.view_settings.view_transform = "Standard"; sc.view_settings.look = "None"

def clear_scene():
    for ob in list(bpy.data.objects): bpy.data.objects.remove(ob)
    for c in list(bpy.data.collections): bpy.data.collections.remove(c)
    for blk in (bpy.data.meshes, bpy.data.materials, bpy.data.cameras, bpy.data.lights):
        for d in list(blk): blk.remove(d)
    _mats.clear(); SITE.clear()

if __name__ == "__main__" and not bpy.app.background:
    clear_scene(); build_all(); setup_render(1600, 1200, 32)
elif __name__ == "__main__":
    argv = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
    args = [a for a in argv if not a.startswith("-")]
    stage = args[0] if args else "save"
    W = int(args[1]) if len(args) > 1 else 1600; H = int(args[2]) if len(args) > 2 else 1200
    SPP = int(args[3]) if len(args) > 3 else 32
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "renders"); os.makedirs(out, exist_ok=True)
    reset()
    per, nt, npl, eg = build_all()
    setup_render(W, H, SPP)
    lib = bpy.data.collections["Library"]
    pois = {k: project(lib[f"poi_{k}"]) for k in ("north", "south", "corner", "atrium", "roof")}
    pois["lake"] = project((-95, 95, 0))
    if eg: pois["egrets"] = project(eg[3])
    json.dump({"w": W, "h": H, "pois": pois, "trees": nt, "people": npl, "egrets": len(eg), "anim": anim_data()}, open(f"{out}/meta_{W}.json", "w"))
    print("STATS trees", nt, "people", npl)
    if len(args) > 4:   # 局部渲染：x0 y0 x1 y1（0–1，左下为原点）
        sc = bpy.context.scene; sc.render.use_border = True; sc.render.use_crop_to_border = True
        sc.render.border_min_x, sc.render.border_min_y, sc.render.border_max_x, sc.render.border_max_y = map(float, args[4].split(","))
    if stage in ("scene", "preview"):
        name = os.environ.get("OUT_NAME") or f"scene_{W}"          # OUT_NAME：另存为其他文件名（对比图用，不覆盖正式全图）
        bpy.context.scene.render.filepath = f"{out}/{name}.png"; bpy.ops.render.render(write_still=True)
    elif stage == "mask": mask_mode(); bpy.context.scene.render.filepath = f"{out}/mask_{W}.png"; bpy.ops.render.render(write_still=True)
    elif stage == "vis": vis_mode(); bpy.context.scene.render.filepath = f"{out}/vis_{W}.png"; bpy.ops.render.render(write_still=True)
    elif stage == "save": bpy.ops.wm.save_as_mainfile(filepath=os.path.join(os.path.dirname(os.path.abspath(__file__)), "suibe_library.blend"))
