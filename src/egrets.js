/*
 * 白鹭动画层：在 Leaflet 地图上叠加一个 canvas，按世界坐标（米）驱动白鹭，
 * 再用底图相机的投影矩阵换算到屏幕。行为参考白鹭习性：
 *   - 飞行：缩颈、腿后伸，振翅慢而均匀，下降时滑翔
 *   - 降落：抬翅减速（flare），落在浅水处，水面起涟漪
 *   - 觅食：慢慢踱步 → 静立等待 → 突然低头啄食
 *   - 起飞：快速振翅，向湖心方向爬升
 * 4 只：两只结伴（leader + follower）、一只独行盘旋、一只以涉水觅食为主偶尔短距离低飞。
 */
(function () {
  const A = DATA.anim, SP = SPRITES.egrets2, SR = SPRITES.egrets2_refl;
  const MAXZ = window.EG_MAXZ, PXM_IMG = window.EG_PXM;      // 底图每米像素（相机平面）
  const SPR_SCALE = PXM_IMG / SP.pxm, REF_SCALE = PXM_IMG / SR.pxm;   // 精灵图 → 底图像素
  const P = A.proj, SUN = A.sun, SPOTS = A.spots;
  const POSE = Object.fromEntries(SP.cols.map((p, i) => [p, i])), RPOSE = Object.fromEntries(SR.cols.map((p, i) => [p, i]));
  const GROUND = new Set(['stand', 'alert', 'stalk', 'strike', 'walk1', 'walk2', 'walk3', 'walk4']);
  const TAU = Math.PI * 2;
  const rand = (a, b) => a + Math.random() * (b - a);
  const pick = arr => arr[Math.floor(Math.random() * arr.length)];
  const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
  const angDiff = (a, b) => { let d = (b - a) % TAU; if (d > Math.PI) d -= TAU; if (d < -Math.PI) d += TAU; return d; };
  const LAKE_C = A.lake.reduce((s, p) => [s[0] + p[0] / A.lake.length, s[1] + p[1] / A.lake.length], [0, 0]);
  const WZ = A.waterZ || 0;                       // 水面高度（湖面比地面低）
  const STAND_Z = SP.stand_height + WZ;

  // ---------------------------------------------------------------- 画布
  const map = window.EG_MAP;
  const cv = document.createElement('canvas');
  cv.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:450';
  map.getContainer().appendChild(cv);
  const ctx = cv.getContext('2d');
  const sheet = new Image(); sheet.src = 'sprites/egrets2.webp';
  const rsheet = new Image(); rsheet.src = 'sprites/egrets2_refl.webp';
  let dpr = 1;
  function resize() {
    dpr = Math.min(window.devicePixelRatio || 1, 2);
    const s = map.getSize(); cv.width = s.x * dpr; cv.height = s.y * dpr; cv.style.width = s.x + 'px'; cv.style.height = s.y + 'px';
  }
  resize(); map.on('resize', resize);
  let zooming = false;
  map.on('zoomanim', () => { zooming = true; cv.style.opacity = 0; });
  map.on('zoomend', () => { zooming = false; cv.style.transition = 'opacity .25s'; cv.style.opacity = 1; });

  // 世界坐标 → 屏幕（容器像素）
  let O, sc;
  function frameXform() { O = map.latLngToContainerPoint([0, 0]); sc = Math.pow(2, map.getZoom() - MAXZ); }
  const toImg = (x, y, z) => [P[0][0] + x * P[1][0] + y * P[2][0] + z * P[3][0], P[0][1] + x * P[1][1] + y * P[2][1] + z * P[3][1]];
  const toScr = (x, y, z) => { const p = toImg(x, y, z); return [O.x + p[0] * sc, O.y + p[1] * sc]; };
  function groundXform() {   // 让 ctx 直接用“地面米”作画（阴影、涟漪）
    ctx.setTransform(dpr * sc * P[1][0], dpr * sc * P[1][1], dpr * sc * P[2][0], dpr * sc * P[2][1],
                     dpr * (O.x + sc * (P[0][0] + WZ * P[3][0])), dpr * (O.y + sc * (P[0][1] + WZ * P[3][1])));
  }

  // ---------------------------------------------------------------- 白鹭
  const ripples = [];
  function ripple(x, y, big) { ripples.push({x, y, t: 0, max: big ? 3.2 : 1.8}); }

  class Egret {
    constructor(name) {
      this.name = name; this.x = 0; this.y = 0; this.z = STAND_Z; this.h = 0; this.pose = 'stand';
      this.flap = Math.random() * TAU; this.q = []; this.spot = null; this.alpha = 1;
    }
    get standing() { return GROUND.has(this.pose); }
    update(dt) {
      while (this.q.length) {
        const act = this.q[0];
        if (!act.started) { act.started = true; act.init && act.init(this); }
        if (act.step(this, dt)) { this.q.shift(); continue; }
        break;
      }
    }
    flapPose(dt, hz, glide) {
      // 滑翔前先把当前这一下扇完，再平展双翼
      if (glide && this.pose === 'glide') return;
      const before = this.flap;
      this.flap += TAU * hz * dt;
      if (glide && Math.floor(before / TAU) !== Math.floor(this.flap / TAU)) { this.pose = 'glide'; return; }
      this.pose = 'flap' + (Math.floor((this.flap / TAU % 1) * SP.flap) % SP.flap);
    }
  }

  // ---- 动作（每个动作 step 返回 true 表示完成）
  function circle(cx, cy, r, turns, z0, z1, dir = 1, speed = 8) {
    let a0, t = 0, T;
    return {
      init(b) { a0 = Math.atan2(b.y - cy, b.x - cx); T = turns * TAU * r / speed; },
      step(b, dt) {
        t += dt; const k = Math.min(t / T, 1), a = a0 + dir * k * turns * TAU;
        const nx = cx + r * Math.cos(a), ny = cy + r * Math.sin(a), nz = z0 + (z1 - z0) * k + Math.sin(t * 0.7) * 0.8;
        b.h = Math.atan2(ny - b.y, nx - b.x) || b.h; const descending = nz < b.z - 0.02;
        b.x = nx; b.y = ny; b.z = nz;
        // 盘旋中：振翅 2–3 秒，滑翔 1–2 秒交替
        const cyc = (t + b.flap) % 4.2; b.flapPose(dt, 2.3, descending || cyc > 2.8);
        return k >= 1;
      }
    };
  }
  function flyTo(tx, ty, tz, speed = 8.5, turn = 1.7) {
    return {
      step(b, dt) {
        const want = Math.atan2(ty - b.y, tx - b.x), d = angDiff(b.h, want);
        b.h += clamp(d, -turn * dt, turn * dt);
        b.x += Math.cos(b.h) * speed * dt; b.y += Math.sin(b.h) * speed * dt;
        const dist = Math.hypot(tx - b.x, ty - b.y), dz = tz - b.z;
        b.z += clamp(dz, -1.8 * dt, 1.8 * dt) * Math.min(1, 40 / Math.max(dist, 1));
        b.flapPose(dt, 2.4, dz < -2 && dist < 60);
        return dist < 7;
      }
    };
  }
  function land(spot) {            // 低空进近 → 抬翅减速 → 落入浅水
    let ax, ay, t = 0; const T = 2.4;
    let sx, sy, sz;
    return {
      init(b) { const dx = spot.n[0], dy = spot.n[1]; ax = spot.p[0] + dx * 14; ay = spot.p[1] + dy * 14; },
      step(b, dt) {
        if (sx === undefined) {
          const f = flyTo(ax, ay, 4.5, 7.5, 1.6);
          if (!f.step(b, dt)) return false;
          sx = b.x; sy = b.y; sz = b.z; b.h = Math.atan2(spot.p[1] - sy, spot.p[0] - sx);
        }
        t += dt; const k = Math.min(t / T, 1), e = 1 - Math.pow(1 - k, 2);
        b.x = sx + (spot.p[0] - sx) * e; b.y = sy + (spot.p[1] - sy) * e; b.z = sz + (STAND_Z - sz) * e;
        b.pose = k < 0.3 ? 'glide' : k < 0.55 ? 'flare1' : k < 0.8 ? 'flare2' : 'flare3';
        if (k >= 1) { b.pose = 'stand'; b.spot = spot.i; ripple(b.x, b.y, true); b.h += rand(-0.6, 0.6); return true; }
        return false;
      }
    };
  }
  function forage(duration) {      // 踱步 / 静立 / 啄食 循环
    let t = 0, sub = null, st = 0, dirIdx = 1, walkT = 0;
    let still = 'stand';
    const next = () => {
      const r = Math.random(); sub = r < 0.36 ? 'walk' : r < 0.66 ? 'still' : 'stalk';
      st = sub === 'walk' ? rand(2.5, 5) : sub === 'still' ? rand(1.5, 4) : rand(0.7, 1.6);
      still = Math.random() < 0.3 ? 'alert' : 'stand';                    // 偶尔伸长脖子张望
      if (sub === 'walk' && Math.random() < 0.4) dirIdx *= -1;
    };
    return {
      init(b) { next(); },
      step(b, dt) {
        t += dt; st -= dt;
        if (sub === 'walk') {
          // 沿湖岸浅水慢慢走（约 0.35 m/s）
          let i = b.spot + dirIdx * 0.35 * dt / 2.0;      // 湖岸点间距 2 m
          const i0 = Math.floor(i), i1 = i0 + 1;
          if (i0 < 0 || i1 >= SPOTS.length || SPOTS[i0].gap) { dirIdx *= -1; i = b.spot; }
          b.spot = clamp(i, 0, SPOTS.length - 1.001);
          const a = SPOTS[Math.floor(b.spot)], c = SPOTS[Math.min(Math.floor(b.spot) + 1, SPOTS.length - 1)], f = b.spot % 1;
          const nx = a.p[0] + (c.p[0] - a.p[0]) * f, ny = a.p[1] + (c.p[1] - a.p[1]) * f;
          if (Math.hypot(nx - b.x, ny - b.y) > 1e-4) b.h = Math.atan2(ny - b.y, nx - b.x);
          b.x = nx; b.y = ny; walkT += dt;
          const was = b.pose; b.pose = 'walk' + (Math.floor(walkT * 3.4) % 4 + 1);
          if (b.pose !== was && (b.pose === 'walk2' || b.pose === 'walk4') && Math.random() < 0.35) ripple(b.x, b.y, false);
        } else if (sub === 'still') { b.pose = still; }
        else if (sub === 'stalk') { b.pose = 'stalk'; }                      // 压低身体、伸颈瞄准
        else { b.pose = 'strike'; if (st < 0.3 && !b._rp) { ripple(b.x + Math.cos(b.h) * 1.1, b.y + Math.sin(b.h) * 1.1, false); b._rp = true; } }
        if (st <= 0) { b._rp = false; if (sub === 'stalk') { sub = 'strike'; st = 0.5; } else next(); }
        if (t >= duration && sub !== 'strike') { b.pose = 'stand'; return true; }
        return false;
      }
    };
  }
  function takeoff(height = 9) {   // 快速振翅，朝湖心爬升
    let t = 0, v = 1; const T = 2.4;
    return {
      init(b) { b.h = Math.atan2(LAKE_C[1] - b.y, LAKE_C[0] - b.x) + rand(-0.5, 0.5); ripple(b.x, b.y, true); },
      step(b, dt) {
        t += dt; v = Math.min(8, v + 4 * dt);
        b.x += Math.cos(b.h) * v * dt; b.y += Math.sin(b.h) * v * dt; b.z += (height - STAND_Z) / T * dt;
        b.flapPose(dt, 3.6, false); return t >= T;
      }
    };
  }
  const wait = s => { let t = 0; return { step(b, dt) { t += dt; return t >= s; } }; };

  // ---- 湖岸落点
  function spotsFar(from, minD) {
    const c = SPOTS.filter(s => !s.gap && (from == null || Math.hypot(s.p[0] - SPOTS[from | 0].p[0], s.p[1] - SPOTS[from | 0].p[1]) > minD));
    return c.length ? pick(c) : pick(SPOTS);
  }
  function spotsRange(from, dmin, dmax) {
    const o = SPOTS[from | 0].p;
    const c = SPOTS.filter(s => !s.gap && Math.hypot(s.p[0] - o[0], s.p[1] - o[1]) > dmin && Math.hypot(s.p[0] - o[0], s.p[1] - o[1]) < dmax);
    return c.length ? pick(c) : spotsFar(from, dmin);
  }
  function neighbor(spot, steps) {
    const j = clamp(spot.i + steps, 0, SPOTS.length - 1);
    for (let k = spot.i; k !== j; k += Math.sign(steps)) if (SPOTS[Math.min(k, k + Math.sign(steps))].gap) return spot;
    return SPOTS[j];
  }
  function place(b, spot) { b.x = spot.p[0]; b.y = spot.p[1]; b.z = STAND_Z; b.spot = spot.i; b.h = Math.atan2(spot.t[1], spot.t[0]); b.pose = 'stand'; }
  function overLake(r = 0.55) { return [LAKE_C[0] + rand(-30, 30) * r * 2, LAKE_C[1] + rand(-25, 25) * r * 2]; }

  // ---------------------------------------------------------------- 4 只白鹭的行为脚本
  const birds = [];
  // 1+2：结伴
  const L1 = new Egret('pair-a'), L2 = new Egret('pair-b'); birds.push(L1, L2);
  { const s = spotsFar(null, 0); place(L1, s); place(L2, neighbor(s, 2)); }
  function pairCycle(first) {
    const c = overLake(), r = rand(28, 38), dir = Math.random() < .5 ? 1 : -1, zc = rand(18, 26);
    const s = spotsFar(L1.spot, 40), s2 = neighbor(s, Math.random() < .5 ? 2 : -2);
    const fdur = rand(30, 50);
    L1.q.push(first ? forage(rand(6, 10)) : wait(0), takeoff(), flyTo(c[0] + r, c[1], zc), circle(c[0], c[1], r, rand(1.0, 1.5), zc, 8, dir), land(s), forage(fdur));
    L2.q.push(first ? forage(rand(7, 11)) : wait(0), wait(rand(0.4, 1.2)), takeoff(), flyTo(c[0] + r + 5, c[1] - 3, zc + 1.5),
      circle(c[0], c[1], r + 5, rand(1.0, 1.5), zc + 1.5, 8, dir), land(s2), forage(fdur + rand(-4, 4)));
  }
  pairCycle(true);
  // 3：独行，较高处大范围盘旋，有时飞到图书馆上空
  const S3 = new Egret('solo'); birds.push(S3);
  S3.x = LAKE_C[0] - 80; S3.y = LAKE_C[1] + 60; S3.z = 30; S3.h = -0.6;
  function soloCycle() {
    const overLib = Math.random() < 0.5;
    const c = overLib ? [-5, 10] : overLake(), r = overLib ? 34 : rand(30, 45), z = rand(28, 40);
    const s = spotsFar(S3.spot, 60);
    S3.q.push(flyTo(c[0] + r * Math.cos(1), c[1] + r * Math.sin(1), z, 9), circle(c[0], c[1], r, rand(1.5, 2.5), z, z - 6, Math.random() < .5 ? 1 : -1, 9),
      flyTo(LAKE_C[0], LAKE_C[1], 14, 8.5), land(s), forage(rand(25, 40)), takeoff(10), flyTo(...overLake(1.4), 24, 9));
  }
  soloCycle();
  // 4：涉水觅食为主，偶尔低飞换个地方
  const W4 = new Egret('wader'); birds.push(W4);
  place(W4, spotsFar(L1.spot, 70));
  function waderCycle() {
    const s = spotsRange(W4.spot, 25, 70);
    const mid = [(W4.x + s.p[0]) / 2 + s.n[0] * 18, (W4.y + s.p[1]) / 2 + s.n[1] * 18];
    W4.q.push(forage(rand(40, 70)), takeoff(5), flyTo(mid[0], mid[1], 6, 7), land(s));
  }
  waderCycle();

  // ---------------------------------------------------------------- 绘制
  const cell = SP.cell, anc = SP.anchor;
  function drawBird(b) {
    const hi = ((Math.round(b.h / TAU * SP.headings) % SP.headings) + SP.headings) % SP.headings;
    const col = POSE[b.pose] ?? POSE.glide;
    const [sx, sy] = toScr(b.x, b.y, b.z);
    const k = SPR_SCALE * sc;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = b.alpha * (window.TOD ? TOD.egret : 1);         // 夜里白鹭归巢（淡出）
    ctx.filter = window.TOD ? TOD.filter : 'none';
    ctx.drawImage(sheet, col * cell, hi * cell, cell, cell, sx - anc[0] * k, sy - anc[1] * k, cell * k, cell * k);
    ctx.globalAlpha = 1; ctx.filter = 'none';
  }
  // 湖面倒影：镜像模型画在 (x, y, 2·水面 − z)，越高越淡；只在湖面上方出现
  const inLake = (x, y) => { let c = false; const L = A.lake; for (let i = 0, j = L.length - 1; i < L.length; j = i++) { const [xi, yi] = L[i], [xj, yj] = L[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c; } return c; };
  const rcell = SR.cell, ranc = SR.anchor;
  function drawReflection(b) {
    const hz = b.z - WZ; if (hz > 14 || !rsheet.complete || !inLake(b.x, b.y)) return;
    const p = b.pose, rp = RPOSE[p] ?? (p === 'flare2' || p === 'flare3' ? RPOSE.flare3 : RPOSE.glide);
    const hi = ((Math.round(b.h / TAU * SR.headings) % SR.headings) + SR.headings) % SR.headings;
    const [sx, sy] = toScr(b.x, b.y, 2 * WZ - b.z), k = REF_SCALE * sc;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = b.alpha * 0.42 * Math.exp(-hz / 5) * (window.TOD ? TOD.egret * (0.4 + 0.6 * TOD.day) : 1);
    ctx.drawImage(rsheet, rp * rcell, hi * rcell, rcell, rcell, sx - ranc[0] * k, sy - ranc[1] * k, rcell * k, rcell * k);
    ctx.globalAlpha = 1;
  }
  function drawShadow(b) {
    // 阴影沿太阳方向投到地面（水面）上，越高越淡；随时间滑块转动，夜里消失
    const SUN = window.TOD ? TOD.sun : A.sun, SH = window.TOD ? TOD.shadow : 1;
    if (SH < 0.02) return;
    ctx.fillStyle = '#0b1f24';
    if (!b.standing) {
      const hz = b.z - WZ, gx = b.x - SUN[0] * hz / SUN[2], gy = b.y - SUN[1] * hz / SUN[2];
      const span = (b.pose === 'glide' ? 1.9 : 1.6) * A.scale.egret * 0.55, len = 0.55 * A.scale.egret;
      ctx.globalAlpha = 0.22 * Math.exp(-b.z / 45) * SH * b.alpha;
      ctx.beginPath(); ctx.ellipse(gx, gy, len, span, b.h, 0, TAU); ctx.fill();
    } else {
      // 站立：细长的影子沿阳光方向从脚下拉出（身体 + 颈部）
      const H = 1.9 * A.scale.egret * 0.5, k = H / SUN[2], dx = -SUN[0] * k, dy = -SUN[1] * k;   // 站在水面上
      const ang = Math.atan2(dy, dx), L = Math.hypot(dx, dy) / 2;
      ctx.globalAlpha = 0.2 * SH * b.alpha;
      ctx.beginPath(); ctx.ellipse(b.x + dx / 2, b.y + dy / 2, L, 0.16 * A.scale.egret, ang, 0, TAU); ctx.fill();
      ctx.globalAlpha = 0.16 * SH * b.alpha;
      ctx.beginPath(); ctx.ellipse(b.x + dx * 0.55, b.y + dy * 0.55, 0.45 * A.scale.egret, 0.2 * A.scale.egret, b.h, 0, TAU); ctx.fill();
    }
    ctx.globalAlpha = 1;
  }
  function drawRipples(dt) {
    ctx.lineWidth = 0.09;
    for (let i = ripples.length - 1; i >= 0; i--) {
      const r = ripples[i]; r.t += dt; const k = r.t / 1.8;
      if (k >= 1) { ripples.splice(i, 1); continue; }
      ctx.strokeStyle = `rgba(235,248,255,${0.55 * (1 - k)})`;
      ctx.beginPath(); ctx.arc(r.x, r.y, 0.25 + r.max * k, 0, TAU); ctx.stroke();
      if (k > 0.25) { ctx.beginPath(); ctx.arc(r.x, r.y, 0.15 + r.max * (k - 0.25), 0, TAU); ctx.stroke(); }
    }
  }

  function stepAll(dt) {
    for (const b of birds) b.update(dt);
    if (!L1.q.length && !L2.q.length) pairCycle(false);
    if (!S3.q.length) soloCycle();
    if (!W4.q.length) waderCycle();
  }
  let last = performance.now(), playing = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function tick(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (playing) stepAll(dt);
    if (!zooming && sheet.complete) {
      frameXform();
      ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, cv.width, cv.height);
      for (const b of birds) drawReflection(b);
      groundXform(); drawRipples(playing ? dt : 0); for (const b of birds) drawShadow(b);
      const order = birds.slice().sort((a, b) => toImg(a.x, a.y, 0)[1] - toImg(b.x, b.y, 0)[1]);
      for (const b of order) drawBird(b);
    }
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);

  // 播放 / 暂停 + 跟随
  window.EG = {
    birds,
    toggle() { playing = !playing; return playing; },
    get playing() { return playing; },
    pos(i) { const b = birds[i]; return toImg(b.x, b.y, b.z); },
    simulate(seconds, dt = 0.05) { for (let t = 0; t < seconds; t += dt) stepAll(dt); },
  };
})();
