"""
风格化树木（第二版）：融球（metaball）做圆润的树冠 + 噪声位移 + 平滑着色 + 颜色渐变材质。
  make_trees(coll) -> {名称: 包含该树的 collection}
  香樟 Camphor / Camphor2、垂柳 Willow、水杉 Sequoia、银杏 Ginkgo、灌木 Shrub
"""
import bpy, bmesh, math, random
from mathutils import Vector, Matrix


def leaf_material(name, base, dark, light, translucency=0.25):
    """树叶：按高度从暗到亮渐变 + 噪声斑驳 + 少量透光"""
    m = bpy.data.materials.new(name); m.use_nodes = True
    nt = m.node_tree; N = nt.nodes; Lk = nt.links
    b = N["Principled BSDF"]; b.inputs["Roughness"].default_value = 0.85
    tc = N.new("ShaderNodeTexCoord")
    sep = N.new("ShaderNodeSeparateXYZ"); Lk.new(tc.outputs["Object"], sep.inputs[0])
    mr = N.new("ShaderNodeMapRange"); mr.inputs["From Min"].default_value = 2.0; mr.inputs["From Max"].default_value = 7.5
    Lk.new(sep.outputs["Z"], mr.inputs["Value"])
    nz = N.new("ShaderNodeTexNoise"); nz.inputs["Scale"].default_value = 1.6; nz.inputs["Detail"].default_value = 4
    Lk.new(tc.outputs["Object"], nz.inputs["Vector"])
    mix = N.new("ShaderNodeMath"); mix.operation = "MULTIPLY_ADD"; mix.inputs[1].default_value = 0.5
    Lk.new(mr.outputs["Result"], mix.inputs[0])
    nz2 = N.new("ShaderNodeMath"); nz2.operation = "MULTIPLY_ADD"; nz2.inputs[1].default_value = 0.6; nz2.inputs[2].default_value = 0.0
    Lk.new(nz.outputs["Fac"], nz2.inputs[0]); Lk.new(nz2.outputs[0], mix.inputs[2])
    ramp = N.new("ShaderNodeValToRGB"); el = ramp.color_ramp.elements
    el[0].position = 0.2; el[0].color = (*dark, 1); el[1].position = 1.0; el[1].color = (*light, 1)
    mid = el.new(0.55); mid.color = (*base, 1)
    Lk.new(mix.outputs[0], ramp.inputs["Fac"]); Lk.new(ramp.outputs["Color"], b.inputs["Base Color"])
    try:
        b.inputs["Subsurface Weight"].default_value = translucency * 0.4
        b.inputs["Subsurface Radius"].default_value = (0.3, 0.5, 0.2)
    except KeyError:
        pass
    m.diffuse_color = (*base, 1)
    return m


def bark_material():
    m = bpy.data.materials.new("bark"); m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (0.22, 0.15, 0.10, 1); b.inputs["Roughness"].default_value = 0.9
    return m


def metaball_mesh(name, balls, resolution=0.32, threshold=0.6):
    """balls: [(x, y, z, 半径, (sx, sy, sz))] → 网格"""
    mb = bpy.data.metaballs.new(name + "_mb"); mb.resolution = resolution; mb.render_resolution = resolution; mb.threshold = threshold
    for x, y, z, r, sc in balls:
        sx, sy, sz = sc
        if abs(sz - 1) < 0.05 and abs(sx - 1) < 0.05:
            e = mb.elements.new(); e.co = (x, y, z); e.radius = r; continue
        # 椭球：在 z 方向排一串球来近似（sz>1 拉长，sz<1 压扁时缩小半径并横向排列）
        if sz >= 1:
            n = max(2, int(sz * 2)); L = r * (sz - 1)
            for k in range(n):
                t = k / (n - 1) - 0.5
                e = mb.elements.new(); e.co = (x, y, z + t * L * 2); e.radius = r * (1 - 0.25 * abs(t) * 2)
        else:
            e = mb.elements.new(); e.co = (x, y, z); e.radius = r * (0.6 + 0.4 * sz)
            for k in range(6):
                a = k / 6 * math.tau; rr = r * sx * 0.45
                e = mb.elements.new(); e.co = (x + math.cos(a) * rr, y + math.sin(a) * rr, z - r * 0.05); e.radius = r * 0.7
    ob = bpy.data.objects.new(name + "_mbo", mb); bpy.context.scene.collection.objects.link(ob)
    bpy.context.view_layer.update()
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(ob.evaluated_get(dg)); me.name = name
    bpy.data.objects.remove(ob); bpy.data.metaballs.remove(mb)
    return me


def displace(me, strength, scale, seed, stretch_z=1.0):
    """沿法线做噪声位移，让轮廓不那么光滑；stretch_z>1 时纹理沿竖直方向拉长（垂柳的枝条感）"""
    rnd = random.Random(seed); off = Vector((rnd.uniform(0, 50), rnd.uniform(0, 50), rnd.uniform(0, 50)))
    from mathutils import noise
    me.calc_normals_split() if hasattr(me, "calc_normals_split") else None
    for v in me.vertices:
        p = Vector((v.co.x * scale, v.co.y * scale, v.co.z * scale / stretch_z)) + off
        d = noise.noise(p) * 0.6 + noise.noise(p * 2.3) * 0.4
        v.co += v.normal * d * strength


def branch(bm, p0, p1, r0, r1, seg=6):
    d = Vector(p1) - Vector(p0); L = d.length
    q = d.normalized().to_track_quat("Z", "Y").to_matrix().to_4x4()
    bmesh.ops.create_cone(bm, cap_ends=True, segments=seg, radius1=r0, radius2=r1, depth=L,
                          matrix=Matrix.Translation(Vector(p0) + d / 2) @ q)


def assemble(name, wood_bm, crown_me, mats, coll):
    me = bpy.data.meshes.new(name)
    bm = bmesh.new(); wood_bm.to_mesh(me); bm.from_mesh(me)
    nw = len(bm.faces)
    bm.from_mesh(crown_me)
    for i, f in enumerate(bm.faces):
        f.material_index = 0 if i < nw else 1; f.smooth = i >= nw
    bm.to_mesh(me); bm.free(); wood_bm.free(); bpy.data.meshes.remove(crown_me)
    for m in mats: me.materials.append(m)
    ob = bpy.data.objects.new(name, me); coll.objects.link(ob)
    c = bpy.data.collections.new(name + "_C"); c.objects.link(ob)
    return c


def camphor(name, seed, leaf, bark, coll):
    rnd = random.Random(seed)
    wood = bmesh.new()
    branch(wood, (0, 0, 0), (0, 0, 2.6), 0.32, 0.24)
    tips = []
    for i in range(4):
        a = i / 4 * math.tau + rnd.uniform(-0.3, 0.3); r = rnd.uniform(1.2, 1.8)
        tip = (math.cos(a) * r, math.sin(a) * r, rnd.uniform(4.2, 5.0)); tips.append(tip)
        branch(wood, (0, 0, 2.5), tip, 0.16, 0.07, 5)
    balls = [(0, 0, 5.5, 2.6, (1, 1, 1)), (0, 0, 6.5, 2.1, (1, 1, 1))]
    for x, y, z in tips:
        balls.append((x * 1.2, y * 1.2, z + 0.5, rnd.uniform(1.9, 2.2), (1, 1, 1)))
    for i in range(8):
        a = i / 8 * math.tau + rnd.uniform(-0.2, 0.2); r = rnd.uniform(2.2, 2.7)
        balls.append((math.cos(a) * r, math.sin(a) * r, rnd.uniform(4.8, 5.6), rnd.uniform(1.4, 1.7), (1, 1, 1)))
    for i in range(4):
        a = rnd.uniform(0, math.tau); balls.append((math.cos(a) * 1.2, math.sin(a) * 1.2, rnd.uniform(6.6, 7.2), 1.4, (1, 1, 1)))
    crown = metaball_mesh(name + "_crown", balls)
    displace(crown, 0.32, 1.1, seed)
    return assemble(name, wood, crown, [bark, leaf], coll)


def willow(name, seed, leaf, bark, coll):
    rnd = random.Random(seed)
    wood = bmesh.new()
    branch(wood, (0, 0, 0), (0.3, 0.1, 3.4), 0.28, 0.2)
    for i in range(3):
        a = i / 3 * math.tau + 0.4
        branch(wood, (0.3, 0.1, 3.2), (math.cos(a) * 1.4, math.sin(a) * 1.4, 5.4), 0.15, 0.07, 5)
    # 冠顶 + 一圈向下垂的长椭球（垂枝），下缘参差
    balls = [(0, 0, 6.0, 2.2, (1, 1, 1)), (0, 0, 6.6, 1.8, (1, 1, 1))]
    for i in range(6):
        a = i / 6 * math.tau; balls.append((math.cos(a) * 1.7, math.sin(a) * 1.7, 5.7, 1.5, (1, 1, 1)))
    n = 22
    for i in range(n):
        a = i / n * math.tau + rnd.uniform(-0.08, 0.08)
        L = rnd.uniform(2.8, 4.4); top = 5.6; r0 = 2.55
        k = int(L / 0.45)
        for j in range(k + 1):
            t = j / k; rr = r0 + 0.35 * t                         # 越往下越向外张开一点
            balls.append((math.cos(a) * rr, math.sin(a) * rr, top - L * t, 0.72 * (1 - 0.45 * t), (1, 1, 1)))
    crown = metaball_mesh(name + "_crown", balls, resolution=0.22, threshold=0.55)
    displace(crown, 0.16, 2.6, seed, stretch_z=8.0)       # 竖向拉长的噪声 → 枝条感
    return assemble(name, wood, crown, [bark, leaf], coll)


def sequoia(name, seed, leaf, bark, coll):
    rnd = random.Random(seed)
    wood = bmesh.new(); branch(wood, (0, 0, 0), (0, 0, 3.0), 0.3, 0.22)
    balls = []
    for k in range(14):
        z = 3.2 + k * 0.72; r = 1.75 * (1 - k / 15) + 0.35
        balls.append((rnd.uniform(-.1, .1), rnd.uniform(-.1, .1), z, r, (1, 1, 1)))
    crown = metaball_mesh(name + "_crown", balls)
    displace(crown, 0.18, 1.4, seed)
    return assemble(name, wood, crown, [bark, leaf], coll)


def ginkgo(name, seed, leaf, bark, coll):
    rnd = random.Random(seed)
    wood = bmesh.new(); branch(wood, (0, 0, 0), (0, 0, 3.2), 0.24, 0.18)
    balls = [(0, 0, 5.0, 2.0, (1, 1, 1)), (0, 0, 6.2, 1.8, (1, 1, 1)), (0, 0, 7.2, 1.3, (1, 1, 1))]
    for i in range(5):
        a = i / 5 * math.tau; balls.append((math.cos(a) * 1.1, math.sin(a) * 1.1, rnd.uniform(5.0, 6.0), 1.3, (1, 1, 1)))
    crown = metaball_mesh(name + "_crown", balls)
    displace(crown, 0.22, 1.0, seed)
    return assemble(name, wood, crown, [bark, leaf], coll)


def shrub(name, seed, leaf, bark, coll):
    rnd = random.Random(seed)
    wood = bmesh.new(); branch(wood, (0, 0, 0), (0, 0, 0.3), 0.08, 0.06, 4)
    balls = [(rnd.uniform(-.5, .5), rnd.uniform(-.5, .5), 0.6, rnd.uniform(0.6, 0.8), (1, 1, 0.8)) for _ in range(4)]
    crown = metaball_mesh(name + "_crown", balls, resolution=0.2)
    displace(crown, 0.08, 2.0, seed)
    return assemble(name, wood, crown, [bark, leaf], coll)


def make_trees(coll):
    bark = bark_material()
    L = {
        "camphor": leaf_material("leaf_camphor", (0.06, 0.22, 0.04), (0.015, 0.07, 0.015), (0.18, 0.38, 0.06)),
        "camphor2": leaf_material("leaf_camphor2", (0.09, 0.26, 0.04), (0.02, 0.09, 0.02), (0.24, 0.44, 0.07)),
        "willow": leaf_material("leaf_willow", (0.24, 0.38, 0.06), (0.06, 0.13, 0.02), (0.48, 0.60, 0.14), 0.35),
        "sequoia": leaf_material("leaf_sequoia", (0.45, 0.17, 0.04), (0.16, 0.05, 0.015), (0.66, 0.32, 0.08)),
        "ginkgo": leaf_material("leaf_ginkgo", (0.72, 0.50, 0.04), (0.32, 0.18, 0.015), (0.92, 0.74, 0.14)),
        "shrub": leaf_material("leaf_shrub", (0.07, 0.22, 0.05), (0.02, 0.08, 0.02), (0.18, 0.36, 0.07)),
    }
    return {
        "Camphor": camphor("Camphor", 1, L["camphor"], bark, coll),
        "Camphor2": camphor("Camphor2", 7, L["camphor2"], bark, coll),
        "Willow": willow("Willow", 3, L["willow"], bark, coll),
        "Sequoia": sequoia("Sequoia", 4, L["sequoia"], bark, coll),
        "Ginkgo": ginkgo("Ginkgo", 5, L["ginkgo"], bark, coll),
        "Shrub": shrub("Shrub", 6, L["shrub"], bark, coll),
    }
