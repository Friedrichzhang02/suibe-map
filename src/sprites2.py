"""
精细版动作帧（精灵图）：与底图同一相机、同一太阳与色彩管理，透明背景。

  白鹭（小白鹭：黑嘴、黑腿、黄脚）
    24 个朝向 × 20 种姿态：
      振翅 8 帧（完整一个扇翅周期，上扬时外翼折收）、滑翔、降落 3 帧、
      站立、警觉伸颈、潜行觅食、啄食、踱步 4 帧
    另渲染一张"水中倒影"图（站立 / 涉水 / 低空姿态的镜像，用于湖面倒影）
  学生
    8 种造型（发型、上衣、下装、背包不同）× 12 个朝向 ×
      走路 8 帧、站立、交谈 2 帧、看手机、坐、坐着看书

用法：blender -b -P sprites2.py -- [--egrets] [--students] [--quick]
      不带参数 = 两者都渲染；--quick = 低采样快速预览
"""
import bpy, bmesh, math, json, os, sys
from mathutils import Vector, Matrix
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import suibe as S

ARGS = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
QUICK = "--quick" in ARGS
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sprites")
os.makedirs(OUT, exist_ok=True)

EG_PXM, EG_CELL, EG_HEADINGS = 44, 4.4, 24
EG_FLAP = 8
EG_COLS = [f"flap{i}" for i in range(EG_FLAP)] + ["glide", "flare1", "flare2", "flare3",
           "stand", "alert", "stalk", "strike", "walk1", "walk2", "walk3", "walk4"]
EG_REFL_COLS = ["glide", "flare3", "stand", "alert", "stalk", "strike", "walk1", "walk2", "walk3", "walk4"]
EG_REFL_PXM = 30
STU_PXM, STU_CELL, STU_HEADINGS, STU_WALK = 40, 2.8, 12, 8
STU_COLS = [f"walk{i}" for i in range(STU_WALK)] + ["stand", "talk1", "talk2", "phone", "sit", "read"]

C = S.COLORS
WHITE = (0.95, 0.95, 0.93); BILL = (0.07, 0.07, 0.07); LEG = (0.04, 0.04, 0.04); FOOT = (0.92, 0.72, 0.12)


# ============================================================ 网格工具
def ellip(bm, c, r, rot=Matrix.Identity(4), seg=14, ring=9):
    bmesh.ops.create_uvsphere(bm, u_segments=seg, v_segments=ring, radius=1.0,
                              matrix=Matrix.Translation(c) @ rot @ Matrix.Diagonal((*r, 1)))

def tube(bm, pts, radii, seg=8, caps=True):
    """沿折线生成圆管（半径可变），两端加半球帽"""
    pts = [Vector(p) for p in pts]; rings = []
    for i, p in enumerate(pts):
        t = (pts[min(i + 1, len(pts) - 1)] - pts[max(i - 1, 0)]).normalized()
        a = Vector((1, 0, 0)) if abs(t.x) < 0.9 else Vector((0, 1, 0))
        u = t.cross(a).normalized(); v = t.cross(u)
        r = radii[i] if isinstance(radii, (list, tuple)) else radii
        rings.append([bm.verts.new(p + (u * math.cos(2 * math.pi * k / seg) + v * math.sin(2 * math.pi * k / seg)) * r) for k in range(seg)])
    for a, b in zip(rings, rings[1:]):
        for k in range(seg):
            bm.faces.new((a[k], a[(k + 1) % seg], b[(k + 1) % seg], b[k]))
    if caps:
        r0 = radii[0] if isinstance(radii, (list, tuple)) else radii; r1 = radii[-1] if isinstance(radii, (list, tuple)) else radii
        ellip(bm, pts[0], (r0, r0, r0), seg=8, ring=6); ellip(bm, pts[-1], (r1, r1, r1), seg=8, ring=6)

def capsule(bm, a, b, r, seg=8): tube(bm, [a, b], [r, r], seg)

def box(bm, m, size):
    bmesh.ops.create_cube(bm, size=1.0, matrix=m @ Matrix.Diagonal((*size, 1)))

def rotx(a): return Matrix.Rotation(math.radians(a), 4, 'X')
def roty(a): return Matrix.Rotation(math.radians(a), 4, 'Y')
def rotz(a): return Matrix.Rotation(math.radians(a), 4, 'Z')

def build(name, parts, smooth=True):
    """parts = [(函数, 颜色, 自发光)]；每部分一个材质槽"""
    me = bpy.data.meshes.new(name); bm = bmesh.new()
    for i, part in enumerate(parts):
        fn, col = part[0], part[1]
        b2 = bmesh.new(); fn(b2); tmp = bpy.data.meshes.new("tmp"); b2.to_mesh(tmp); b2.free()
        for p in tmp.polygons: p.material_index = i
        bm.from_mesh(tmp); bpy.data.meshes.remove(tmp)
        m = bpy.data.materials.new(f"{name}_{i}"); m.use_nodes = True; bs = m.node_tree.nodes["Principled BSDF"]
        bs.inputs["Base Color"].default_value = (*col, 1); bs.inputs["Roughness"].default_value = 0.65
        if len(part) > 2 and part[2]:
            bs.inputs["Emission Color"].default_value = (*col, 1); bs.inputs["Emission Strength"].default_value = part[2]
        me.materials.append(m)
    bm.to_mesh(me); bm.free()
    if smooth:
        for p in me.polygons: p.use_smooth = True
    return me


# ============================================================ 白鹭
def wing(bm, side, e, wrist, sweep=0.0, fold=0.0, origin=(0, 0.05, 0.05)):
    """side=±1；e=肩部上扬角；wrist=外翼相对上扬角；sweep=整翼后掠；fold=外翼后折。翼面在本地 (span,chord) 平面"""
    sh = Matrix.Translation(origin) @ rotz(-side * sweep) @ roty(-side * e)
    inner = [(0.04, 0.17), (0.43, 0.15), (0.43, -0.13), (0.30, -0.15), (0.16, -0.17), (0.04, -0.14)]
    outer = [(0.0, 0.15), (0.22, 0.12), (0.46, 0.03), (0.53, -0.03),              # 前缘到翼尖
             (0.47, -0.07), (0.44, -0.04), (0.38, -0.10), (0.34, -0.07), (0.27, -0.13), (0.22, -0.10),
             (0.14, -0.14), (0.08, -0.12), (0.0, -0.13)]                       # 飞羽锯齿
    wr = sh @ Matrix.Translation((side * 0.43, 0, 0)) @ rotz(side * fold) @ roty(-side * wrist)
    for poly, M in ((inner, sh), (outer, wr)):
        vs = [bm.verts.new(M @ Vector((side * x, y, 0))) for x, y in poly]
        bm.faces.new(vs if side > 0 else vs[::-1])

def egret_mesh(pose):
    fly = pose.startswith(("flap", "glide", "flare"))
    def body(bm):
        if fly:
            ellip(bm, (0, 0, 0), (0.10, 0.29, 0.10))
            ellip(bm, (0, -0.30, -0.005), (0.065, 0.12, 0.018))                          # 尾
            if pose.startswith("flap"):
                ph = 2 * math.pi * int(pose[4:]) / EG_FLAP; s, c = math.sin(ph), math.cos(ph)
                e = 6 + 34 * s; up = max(0.0, c)            # 上扬阶段（c>0）外翼折收后掠
                wing(bm, 1, e, -8 - 34 * up, sweep=4 + 10 * up, fold=22 * up); wing(bm, -1, e, -8 - 34 * up, sweep=4 + 10 * up, fold=22 * up)
            elif pose == "glide":
                wing(bm, 1, 4, -7, sweep=6); wing(bm, -1, 4, -7, sweep=6)
            else:
                k = int(pose[-1]); e = [0, 38, 62, 80][k]; w = [0, 6, 14, 20][k]; sw = [0, -6, -12, -14][k]
                wing(bm, 1, e, w, sweep=sw); wing(bm, -1, e, w, sweep=sw)
            # 缩颈：S 形贴近肩部
            tube(bm, [(0, 0.22, 0.03), (0, 0.32, 0.10), (0, 0.38, 0.07), (0, 0.44, 0.09)], [0.055, 0.045, 0.04, 0.038])
            ellip(bm, (0, 0.47, 0.095), (0.042, 0.06, 0.045))
        else:
            tilt = {"stalk": -4, "strike": -14}.get(pose, 22)
            R = rotx(tilt); ellip(bm, (0, 0, 0), (0.10, 0.27, 0.10), R)
            for sg in (-1, 1): ellip(bm, (sg * 0.055, -0.04, 0.025), (0.055, 0.24, 0.075), R)       # 收拢的翅膀
            ellip(bm, (0, -0.27, -0.10 if tilt > 0 else -0.01), (0.06, 0.12, 0.02), R @ rotx(10))   # 尾
            neck = {
                "stand":  [(0, 0.19, 0.10), (0, 0.24, 0.24), (0, 0.18, 0.36), (0, 0.23, 0.48)],
                "alert":  [(0, 0.19, 0.10), (0, 0.24, 0.32), (0, 0.24, 0.52), (0, 0.27, 0.64)],
                "stalk":  [(0, 0.25, 0.04), (0, 0.38, 0.13), (0, 0.48, 0.12), (0, 0.56, 0.06)],
                "strike": [(0, 0.24, -0.02), (0, 0.40, -0.06), (0, 0.50, -0.22), (0, 0.53, -0.34)],
            }.get(pose)
            if neck is None:                           # 踱步：头部随步伐前后点动
                k = int(pose[-1]) - 1; d = 0.035 * math.sin(math.pi * k / 2)
                neck = [(0, 0.19, 0.10), (0, 0.26 + d, 0.22), (0, 0.22 + d, 0.34), (0, 0.29 + 1.6 * d, 0.44)]
            tube(bm, neck, [0.05, 0.04, 0.034, 0.032])
            hx = Vector(neck[-1]); fwd = (hx - Vector(neck[-2])).normalized()
            hd = Vector((0, 1, 0)) if pose in ("stand", "alert") or pose.startswith("walk") else fwd
            ellip(bm, hx + hd * 0.035, (0.038, 0.058, 0.042))
    def bill(bm):
        if fly: a, b = Vector((0, 0.52, 0.09)), Vector((0, 0.68, 0.075))
        else:
            n = egret_neck_tip(pose); hd = n[1]; a = n[0] + hd * 0.045; b = a + hd * 0.17
        tube(bm, [a, b], [0.016, 0.003], seg=6, caps=False)
    def legs(bm):
        for sg in (-1, 1):
            x = 0.035 * sg
            if pose.startswith(("flap", "glide")): tube(bm, [(x, -0.16, -0.04), (x, -0.45, -0.06), (x, -0.74, -0.06)], 0.011, 5, caps=False)
            elif pose.startswith("flare"):
                k = int(pose[-1]); ang = [0, 50, 75, 90][k]
                tip = Vector((x, -0.05, -0.05)) + Vector((0, -math.cos(math.radians(ang)), -math.sin(math.radians(ang)))) * 0.52
                tube(bm, [(x, -0.05, -0.05), tip], 0.011, 5, caps=False)
            elif pose.startswith("walk"):
                k = int(pose[-1]) - 1; ph = math.pi * k / 2 + (0 if sg > 0 else math.pi)
                fy = 0.11 * math.sin(ph); lift = 0.07 * max(0.0, math.cos(ph))
                knee = (x, 0.02 + fy * 0.4, -0.24 + lift * 0.6)
                tube(bm, [(x, -0.02, -0.06), knee, (x, fy, -0.45 + lift)], 0.011, 5, caps=False)
            else:
                tube(bm, [(x, -0.02, -0.06), (x, 0.0, -0.24), (x, 0.0, -0.45)], 0.011, 5, caps=False)
    def feet(bm):
        for sg in (-1, 1):
            x = 0.035 * sg
            if pose.startswith(("flap", "glide")): ellip(bm, (x, -0.77, -0.06), (0.018, 0.04, 0.012), seg=8, ring=5)
            elif pose.startswith("flare"):
                k = int(pose[-1]); ang = math.radians([0, 50, 75, 90][k])
                ellip(bm, Vector((x, -0.05, -0.05)) + Vector((0, -math.cos(ang), -math.sin(ang))) * 0.54, (0.02, 0.045, 0.012), seg=8, ring=5)
            elif pose.startswith("walk"):
                k = int(pose[-1]) - 1; ph = math.pi * k / 2 + (0 if sg > 0 else math.pi)
                ellip(bm, (x, 0.11 * math.sin(ph) + 0.02, -0.45 + 0.07 * max(0.0, math.cos(ph))), (0.022, 0.05, 0.01), seg=8, ring=5)
            else: ellip(bm, (x, 0.02, -0.45), (0.022, 0.05, 0.01), seg=8, ring=5)
    return build(f"egret_{pose}", [(body, WHITE, 0.16), (bill, BILL), (legs, LEG), (feet, FOOT, 0.1)])

def egret_neck_tip(pose):
    """站姿时头部位置与朝向（与 egret_mesh 中一致），供喙定位"""
    neck = {"stand": [(0, 0.18, 0.36), (0, 0.23, 0.48)], "alert": [(0, 0.24, 0.52), (0, 0.27, 0.64)],
            "stalk": [(0, 0.48, 0.12), (0, 0.56, 0.06)], "strike": [(0, 0.50, -0.22), (0, 0.53, -0.34)]}.get(pose)
    if neck is None:
        k = int(pose[-1]) - 1; d = 0.035 * math.sin(math.pi * k / 2)
        neck = [(0, 0.22 + d, 0.34), (0, 0.29 + 1.6 * d, 0.44)]
    a, b = Vector(neck[0]), Vector(neck[1]); fwd = (b - a).normalized()
    hd = Vector((0, 1, -0.08)).normalized() if pose in ("stand", "alert") or pose.startswith("walk") else fwd
    return b + hd * 0.035, hd


# ============================================================ 学生
LOOKS = [  # (发型, 上衣类型, 上衣色, 下装类型, 下装色, 包)
    ("short", "tee", (0.85, 0.15, 0.12), "pants", C["jeans"], "backpack"),
    ("long", "hoodie", (0.93, 0.92, 0.88), "pants", (0.10, 0.10, 0.12), "tote"),
    ("short", "jacket", (0.12, 0.25, 0.55), "pants", C["khaki"], "backpack"),
    ("ponytail", "tee", (0.95, 0.75, 0.15), "skirt", (0.15, 0.18, 0.32), None),
    ("short", "hoodie", (0.20, 0.45, 0.30), "pants", C["jeans"], "backpack"),
    ("bun", "jacket", (0.55, 0.20, 0.35), "pants", (0.85, 0.82, 0.75), "tote"),
    ("short", "tee", (0.95, 0.95, 0.95), "pants", (0.10, 0.10, 0.12), None),
    ("long", "tee", (0.45, 0.65, 0.85), "skirt", (0.85, 0.80, 0.70), "backpack"),
]

def student_mesh(v, pose):
    hair_t, top_t, top_c, bot_t, bot_c, bag_t = LOOKS[v]
    sit = pose in ("sit", "read"); walk = pose.startswith("walk")
    ph = 2 * math.pi * int(pose[4:]) / STU_WALK if walk else 0.0
    bob = 0.025 * abs(math.cos(ph)) if walk else 0.0
    hip = 0.37 if sit else 0.88 + bob
    sh_z = hip + 0.52; neck_z = hip + 0.60; hz = hip + 0.72
    lean = 4 if walk else 0
    def leg_pts(sg):
        x = 0.085 * sg
        if sit: return [(x, 0.0, hip), (x, 0.40, hip), (x, 0.42, 0.04)]
        p = ph + (0 if sg > 0 else math.pi)
        th = math.radians(24 * math.sin(p)) if walk else 0.0
        bend = math.radians(38 * max(0.0, math.cos(p))) if walk else 0.0
        knee = Vector((x, 0.44 * math.sin(th), hip - 0.44 * math.cos(th)))
        sh = th - bend
        ank = knee + Vector((0, 0.42 * math.sin(sh), -0.42 * math.cos(sh)))
        return [(x, 0, hip), tuple(knee), tuple(ank)]
    def legs(bm):
        for sg in (-1, 1):
            pts = leg_pts(sg)
            if bot_t == "skirt": tube(bm, pts[1:] if not sit else pts[1:], 0.055, 8)
            else: tube(bm, pts, [0.075, 0.065, 0.055], 8)
    def skin_legs(bm):                # 穿裙子时露出的小腿
        if bot_t == "skirt":
            for sg in (-1, 1): pts = leg_pts(sg); tube(bm, pts[1:], 0.048, 8)
    def shoes(bm):
        for sg in (-1, 1):
            a = Vector(leg_pts(sg)[-1]); ellip(bm, a + Vector((0, 0.06, -0.02)), (0.055, 0.12, 0.045), seg=8, ring=6)
    def lower(bm):
        if bot_t == "skirt":
            if sit: ellip(bm, (0, 0.18, hip + 0.02), (0.20, 0.26, 0.09))
            else: tube(bm, [(0, 0, hip + 0.10), (0, 0.01, hip - 0.30)], [0.15, 0.24], 12, caps=False); ellip(bm, (0, 0, hip + 0.10), (0.15, 0.13, 0.05))
        else: ellip(bm, (0, 0.02 if sit else 0, hip + 0.02), (0.17, 0.13, 0.11))
    def torso(bm):
        R = Matrix.Translation((0, 0, hip)) @ rotx(lean) @ Matrix.Translation((0, 0, -hip))
        ln = 0.66 if top_t == "jacket" else 0.58
        bmesh.ops.create_uvsphere(bm, u_segments=14, v_segments=9, radius=1.0,
                                  matrix=R @ Matrix.Translation((0, -0.01, hip + ln / 2 - 0.06)) @ Matrix.Diagonal((0.20, 0.13, ln / 2 + 0.02, 1)))
        ellip(bm, (0, 0, sh_z - 0.02), (0.22, 0.12, 0.08))                                  # 肩
        if top_t == "hoodie": ellip(bm, (0, -0.10, neck_z + 0.02), (0.12, 0.07, 0.07))       # 帽兜
    def arm_pts(sg):
        x = 0.24 * sg; s0 = Vector((x, 0, sh_z - 0.03))
        if sit and pose == "read": return [s0, Vector((x * 0.9, 0.16, sh_z - 0.24)), Vector((x * 0.35, 0.30, sh_z - 0.18))]
        if sit: return [s0, Vector((x, 0.08, sh_z - 0.28)), Vector((x * 0.8, 0.28, hip + 0.06))]
        if pose == "phone": return [s0, Vector((x, 0.06, sh_z - 0.28)), Vector((x * 0.25, 0.26, sh_z - 0.18))]
        if pose == "talk1" and sg > 0: return [s0, Vector((x + 0.04, 0.10, sh_z - 0.24)), Vector((x + 0.02, 0.34, sh_z - 0.10))]
        if pose == "talk2": return [s0, Vector((x + 0.03 * sg, 0.08, sh_z - 0.26)), Vector((x + 0.05 * sg, 0.30, sh_z - 0.16))]
        a = math.radians(-20 * math.sin(ph + (math.pi if sg > 0 else 0))) if walk else 0.0
        el = s0 + Vector((0, 0.28 * math.sin(a), -0.28 * math.cos(a)))
        a2 = a + math.radians(18 if walk else 6)
        return [s0, el, el + Vector((0, 0.26 * math.sin(a2), -0.26 * math.cos(a2)))]
    def sleeves(bm):
        for sg in (-1, 1):
            p = arm_pts(sg)
            if top_t == "tee": tube(bm, [p[0], p[0].lerp(p[1], 0.55)], [0.062, 0.058], 8)
            else: tube(bm, p[:2] + [p[1].lerp(p[2], 0.8)], [0.062, 0.056, 0.05], 8)
    def skin(bm):
        for sg in (-1, 1):
            p = arm_pts(sg)
            if top_t == "tee": tube(bm, [p[0].lerp(p[1], 0.5), p[1], p[2]], [0.045, 0.042, 0.04], 8)
            else: ellip(bm, p[2], (0.042, 0.045, 0.05), seg=8, ring=6)
        capsule(bm, (0, 0, neck_z - 0.04), (0, 0, neck_z + 0.06), 0.05)
        hd = Matrix.Translation((0, 0.01, hz)) @ (rotx(-22) if pose in ("phone", "read") else rotx(0))
        ellip(bm, hd @ Vector((0, 0, 0)), (0.105, 0.115, 0.125), hd.to_3x3().to_4x4())
        skin_legs(bm)
    def hair(bm):
        hd = Matrix.Translation((0, 0.01, hz)) @ (rotx(-22) if pose in ("phone", "read") else rotx(0))
        ellip(bm, hd @ Vector((0, -0.02, 0.045)), (0.115, 0.12, 0.10), hd.to_3x3().to_4x4())
        if hair_t == "long": ellip(bm, hd @ Vector((0, -0.07, -0.10)), (0.12, 0.06, 0.20), hd.to_3x3().to_4x4())
        elif hair_t == "ponytail": tube(bm, [hd @ Vector((0, -0.11, 0.06)), hd @ Vector((0, -0.17, -0.06)), hd @ Vector((0, -0.15, -0.20))], [0.04, 0.045, 0.02], 8)
        elif hair_t == "bun": ellip(bm, hd @ Vector((0, -0.08, 0.13)), (0.06, 0.06, 0.055), hd.to_3x3().to_4x4())
    def bag(bm):
        if bag_t == "backpack":
            if sit: box(bm, Matrix.Translation((0.36, 0.20, 0.20)), (0.26, 0.14, 0.36))
            else: ellip(bm, (0, -0.20, hip + 0.30), (0.15, 0.08, 0.20))
        elif bag_t == "tote":
            if sit: box(bm, Matrix.Translation((-0.36, 0.18, 0.18)), (0.08, 0.30, 0.30))
            else: box(bm, Matrix.Translation((-0.27, -0.02, hip - 0.02)), (0.07, 0.30, 0.32))
    def props(bm):
        if pose == "phone": box(bm, Matrix.Translation(arm_pts(1)[2].lerp(arm_pts(-1)[2], 0.5) + Vector((0, 0.03, 0.04))) @ rotx(-30), (0.07, 0.015, 0.14))
        if pose == "read": box(bm, Matrix.Translation((0, 0.32, sh_z - 0.14)) @ rotx(-35), (0.26, 0.02, 0.18))
    parts = [(lower, bot_c), (legs, bot_c if bot_t != "skirt" else C["skin"]), (shoes, (0.12, 0.12, 0.13)),
             (torso, top_c), (sleeves, top_c), (skin, C["skin"]), (hair, C["hair"])]
    if bag_t: parts.append((bag, S.SHIRTS[(v * 3 + 5) % len(S.SHIRTS)]))
    if pose in ("phone", "read"): parts.append((props, (0.12, 0.13, 0.16) if pose == "phone" else (0.92, 0.90, 0.84)))
    return build(f"stu_{v}_{pose}", parts)


# ============================================================ 渲染
def setup(sheet_w, sheet_h, pxm):
    sc = bpy.context.scene
    cd = bpy.data.cameras.new("SpriteCam"); cd.type = "ORTHO"; cd.sensor_fit = "HORIZONTAL"
    cam = bpy.data.objects.new("SpriteCam", cd); sc.collection.objects.link(cam); sc.camera = cam
    cam.rotation_euler = (math.radians(57), 0, math.radians(-140))      # 与底图相同
    R = cam.rotation_euler.to_matrix(); view = R @ Vector((0, 0, -1))
    cam.location = -view * 200; cd.ortho_scale = sheet_w / pxm; cd.clip_end = 1000
    sun_d = bpy.data.lights.new("Sun", "SUN"); sun_d.energy = 4.6; sun_d.angle = math.radians(1.5); sun_d.color = (1.0, 0.88, 0.72)
    sun = bpy.data.objects.new("Sun", sun_d); sc.collection.objects.link(sun)
    sun.rotation_euler = (-Vector(S.SUN_SRC).normalized()).to_track_quat("-Z", "Y").to_euler()
    world = bpy.data.worlds.new("World"); sc.world = world; world.use_nodes = True
    bg = world.node_tree.nodes["Background"]; bg.inputs[0].default_value = (0.55, 0.62, 0.75, 1); bg.inputs[1].default_value = 0.45
    sc.render.engine = "CYCLES"; sc.cycles.samples = 12 if QUICK else 64; sc.cycles.use_denoising = True
    S.use_gpu(sc)
    sc.render.film_transparent = True
    sc.render.resolution_x, sc.render.resolution_y, sc.render.resolution_percentage = sheet_w, sheet_h, 100
    sc.view_settings.view_transform = "AgX"; sc.view_settings.look = "AgX - Medium High Contrast"; sc.view_settings.exposure = 0.3
    sc.render.image_settings.file_format = "PNG"; sc.render.image_settings.color_mode = "RGBA"
    return R @ Vector((1, 0, 0)), R @ Vector((0, 1, 0))

def place(me, right, up, W, H, pxm, col, row, cell, rot_z, anchor_up=0.0, scale=1.0, mirror=False):
    wm, hm = W / pxm, H / pxm
    c = right * ((col + 0.5) * cell - wm / 2) + up * (hm / 2 - (row + 0.5) * cell) - up * anchor_up
    ob = bpy.data.objects.new(me.name, me); bpy.context.scene.collection.objects.link(ob)
    ob.location = c; ob.rotation_euler = (0, 0, rot_z); ob.scale = (scale, scale, -scale if mirror else scale)
    ob.visible_shadow = False

def render(path):
    if "--meta-only" in ARGS: return
    bpy.context.scene.render.filepath = path; bpy.ops.render.render(write_still=True)

def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True); S._mats.clear()


if __name__ == "__main__":
    want_e = "--egrets" in ARGS or "--students" not in ARGS
    want_s = "--students" in ARGS or "--egrets" not in ARGS
    mp = f"{OUT}/sprites2.json"
    meta = json.load(open(mp)) if os.path.exists(mp) else {}
    meta["version"] = 2
    if want_e:
        for name, cols, pxm, mirror in (("egrets2", EG_COLS, EG_PXM, False), ("egrets2_refl", EG_REFL_COLS, EG_REFL_PXM, True)):
            reset(); meshes = {p: egret_mesh(p) for p in cols} if "--meta-only" not in ARGS else {}
            cell = round(EG_CELL * pxm); W, H = len(cols) * cell, EG_HEADINGS * cell
            right, up = setup(W, H, pxm)
            for c, p in enumerate(cols if meshes else []):
                for h in range(EG_HEADINGS):
                    place(meshes[p], right, up, W, H, pxm, c, h, cell / pxm, 2 * math.pi * h / EG_HEADINGS - math.pi / 2,
                          scale=S.EGRET_SCALE, mirror=mirror)
            render(f"{OUT}/{name}.png")
            meta[name] = {"pxm": pxm, "cell": cell, "headings": EG_HEADINGS, "cols": cols, "anchor": [cell / 2, cell / 2],
                          "stand_height": 0.45 * S.EGRET_SCALE, "flap": EG_FLAP}
            print("rendered", name, W, H)
    if want_s:
        reset()
        cell = round(STU_CELL * STU_PXM); W, H = len(STU_COLS) * cell, len(LOOKS) * STU_HEADINGS * cell
        right, up = setup(W, H, STU_PXM)
        for v in (range(len(LOOKS)) if "--meta-only" not in ARGS else []):
            for c, p in enumerate(STU_COLS):
                me = student_mesh(v, p)
                for h in range(STU_HEADINGS):
                    place(me, right, up, W, H, STU_PXM, c, v * STU_HEADINGS + h, cell / STU_PXM,
                          2 * math.pi * h / STU_HEADINGS - math.pi / 2, anchor_up=1.0, scale=S.PEOPLE_SCALE)
        render(f"{OUT}/students2.png")
        meta["students2"] = {"pxm": STU_PXM, "cell": cell, "variants": len(LOOKS), "headings": STU_HEADINGS, "walk": STU_WALK,
                             "cols": STU_COLS, "anchor": [cell / 2, cell / 2 + 1.0 * STU_PXM]}
        print("rendered students2", W, H)
    json.dump(meta, open(mp, "w"), indent=1)
    print("sprites2 done")
