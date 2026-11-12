/*
 * 湖面波光层：在底图之上、白鹭和学生之下，只画在湖面范围内。
 *   - 波光：阳光在细浪上的反射，一闪即逝（每个亮点有自己的节奏），被风慢慢推着走
 *   - 细浪：顺风向的短条纹，亮边 + 暗边，缓慢漂移、淡入淡出
 * 亮点更集中在"太阳那一侧"的水面，被树或建筑挡住的地方不画。
 */
(function () {
  const A = DATA.anim, P = A.proj, W = DATA.w, H = DATA.h, WZ = A.waterZ || 0, SUN = A.sun;
  const MAXZ = window.EG_MAXZ, TAU = Math.PI * 2, rand = (a, b) => a + Math.random() * (b - a);
  const LAKE = A.lake;
  const WIND = [Math.cos(-Math.PI / 4), Math.sin(-Math.PI / 4)];   // 风向：西北风吹向东南，波峰在画面上呈水平
  const WIND_V = 0.35;

  // ---------------------------------------------------------------- 画布
  const map = window.EG_MAP;
  const cv = document.createElement('canvas');
  cv.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:440';
  map.getContainer().appendChild(cv);
  const ctx = cv.getContext('2d');
  let dpr = 1;
  function resize() { dpr = Math.min(window.devicePixelRatio || 1, 2); const s = map.getSize(); cv.width = s.x * dpr; cv.height = s.y * dpr; cv.style.width = s.x + 'px'; cv.style.height = s.y + 'px'; }
  resize(); map.on('resize', resize);
  let zooming = false;
  map.on('zoomanim', () => { zooming = true; cv.style.opacity = 0; });
  map.on('zoomend', () => { zooming = false; cv.style.transition = 'opacity .25s'; cv.style.opacity = 1; });
  const toImg = (x, y, z) => [P[0][0] + x * P[1][0] + y * P[2][0] + z * P[3][0], P[0][1] + x * P[1][1] + y * P[2][1] + z * P[3][1]];

  // 可见性（地面 = 白）：被树冠或建筑挡住的湖面不画波光
  let vis = null, vw = 0, vh = 0;
  const vimg = new Image(); vimg.src = 'sprites/vis.png';
  vimg.onload = () => { const c = document.createElement('canvas'); vw = c.width = vimg.width; vh = c.height = vimg.height; const g = c.getContext('2d'); g.drawImage(vimg, 0, 0); vis = g.getImageData(0, 0, vw, vh).data; };
  const visible = (x, y) => { if (!vis) return true; const p = toImg(x, y, WZ), u = Math.round(p[0] * vw / W), v = Math.round(p[1] * vh / H); return u >= 0 && v >= 0 && u < vw && v < vh && vis[(v * vw + u) * 4] > 127; };

  // ---------------------------------------------------------------- 湖面几何
  function inLake(x, y) { let c = false; for (let i = 0, j = LAKE.length - 1; i < LAKE.length; j = i++) { const [xi, yi] = LAKE[i], [xj, yj] = LAKE[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c; } return c; }
  function shoreDist(x, y) {
    let best = 1e9;
    for (let i = 0, j = LAKE.length - 1; i < LAKE.length; j = i++) {
      const [ax, ay] = LAKE[j], [bx, by] = LAKE[i], dx = bx - ax, dy = by - ay, L2 = dx * dx + dy * dy || 1;
      const t = Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / L2)); best = Math.min(best, Math.hypot(ax + dx * t - x, ay + dy * t - y));
    }
    return best;
  }
  const xs = LAKE.map(p => p[0]), ys = LAKE.map(p => p[1]);
  const BX0 = Math.min(...xs), BX1 = Math.max(...xs), BY0 = Math.min(...ys), BY1 = Math.max(...ys);
  // 太阳那一侧：水平方向上朝向太阳的湖岸附近，反光更多
  const SUNH = [SUN[0], SUN[1]], SL = Math.hypot(SUNH[0], SUNH[1]) || 1;
  const cx = xs.reduce((a, b) => a + b) / xs.length, cy = ys.reduce((a, b) => a + b) / ys.length;
  const span = Math.max(BX1 - BX0, BY1 - BY0) / 2;
  function sunWeight(x, y) { const s = ((x - cx) * SUNH[0] + (y - cy) * SUNH[1]) / SL / span; return 0.45 + 0.55 * Math.max(0, Math.min(1, 0.5 + s)); }
  function randomWaterPoint(margin = 1.2) {
    for (let k = 0; k < 60; k++) {
      const x = rand(BX0, BX1), y = rand(BY0, BY1);
      if (inLake(x, y) && shoreDist(x, y) > margin && Math.random() < sunWeight(x, y) && visible(x, y)) return [x, y];
    }
    return null;
  }

  // ---------------------------------------------------------------- 路灯在水中的倒影（夜里闪烁）
  // 灯在 (x, y, z)，倒影的屏幕位置 = 镜像点 (x, y, 2·水面 − z)；对应的水面点用投影矩阵解出来，只保留落在湖面、未被遮挡的
  const lampRefl = [];
  (A.lamps || []).forEach(([x, y, z]) => {
    const h = z - WZ, a = P[1][0], b = P[2][0], c = P[1][1], d = P[2][1], det = a * d - b * c;
    const rx = -h * P[3][0], ry = -h * P[3][1], dx = (rx * d - b * ry) / det, dy = (a * ry - rx * c) / det;
    const wx = x + dx, wy = y + dy;
    if (inLake(wx, wy) && shoreDist(wx, wy) > 0.6) lampRefl.push({x, y, z, wx, wy, ph: Math.random() * 10, sp: rand(0.6, 1.4)});
  });
  let lampVisChecked = false;

  // ---------------------------------------------------------------- 波光与细浪
  const GLINTS = 480, WAVES = 260;
  const glints = [], waves = [];
  function newGlint(g = {}) {
    const p = randomWaterPoint(); if (!p) return null;
    g.x = p[0]; g.y = p[1]; g.t = rand(0, 1); g.period = rand(0.9, 2.6); g.size = rand(0.10, 0.28); g.peak = rand(0.55, 1.0);
    g.life = rand(4, 12); return g;
  }
  function newWave(w = {}) {
    const p = randomWaterPoint(2.0); if (!p) return null;
    w.x = p[0]; w.y = p[1]; w.len = rand(2.5, 7); w.t = 0; w.dur = rand(5, 11); w.a = rand(0.12, 0.26);
    w.ang = Math.atan2(WIND[1], WIND[0]) + Math.PI / 2 + rand(-0.25, 0.25);   // 波峰垂直于风向
    w.bend = rand(-0.25, 0.25); return w;
  }
  function fill() {
    while (glints.length < GLINTS) { const g = newGlint(); if (!g) break; glints.push(g); }
    while (waves.length < WAVES) { const w = newWave(); if (!w) break; w.t = rand(0, w.dur); waves.push(w); }
  }

  function step(dt) {
    for (const g of glints) {
      g.t += dt / g.period; g.life -= dt;
      g.x += WIND[0] * WIND_V * dt; g.y += WIND[1] * WIND_V * dt;
      if (g.life <= 0 || !inLake(g.x, g.y)) newGlint(g);
    }
    for (const w of waves) {
      w.t += dt; w.x += WIND[0] * WIND_V * 0.8 * dt; w.y += WIND[1] * WIND_V * 0.8 * dt;
      if (w.t >= w.dur || !inLake(w.x, w.y)) newWave(w);
    }
  }

  // 地面（水面高度）米 → 屏幕像素 的仿射变换
  let O, sc;
  function groundXform() {
    ctx.setTransform(dpr * sc * P[1][0], dpr * sc * P[1][1], dpr * sc * P[2][0], dpr * sc * P[2][1],
                     dpr * (O.x + sc * (P[0][0] + WZ * P[3][0])), dpr * (O.y + sc * (P[0][1] + WZ * P[3][1])));
  }
  function draw() {
    O = map.latLngToContainerPoint([0, 0]); sc = Math.pow(2, map.getZoom() - MAXZ);
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, cv.width, cv.height);
    if (sc < 0.12) return;                                         // 缩得很小时不画（看不清，省电）
    const px = Math.max(0.8, window.EG_PXM * sc);
    const T = window.TOD, dayK = T ? 0.35 + 0.65 * T.day : 1, glintK = T ? T.glint : 1, warm = T ? T.warm : 0, lampK = T ? T.lamp : 0;
    groundXform();
    ctx.save();
    ctx.beginPath(); LAKE.forEach(([x, y], i) => i ? ctx.lineTo(x, y) : ctx.moveTo(x, y)); ctx.closePath(); ctx.clip();
    // 细浪：亮边 + 稍后一点的暗边
    ctx.lineCap = 'round';
    for (const w of waves) {
      const k = w.t / w.dur, a = w.a * Math.sin(Math.PI * k) * dayK, c = Math.cos(w.ang), s = Math.sin(w.ang), h = w.len / 2;
      const x0 = w.x - c * h, y0 = w.y - s * h, x1 = w.x + c * h, y1 = w.y + s * h, mx = w.x + WIND[0] * w.bend, my = w.y + WIND[1] * w.bend;
      ctx.lineWidth = 0.09; ctx.strokeStyle = `rgba(225,242,248,${a})`;
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.quadraticCurveTo(mx, my, x1, y1); ctx.stroke();
      ctx.lineWidth = 0.12; ctx.strokeStyle = `rgba(8,30,36,${a * 0.6})`;
      const ox = -WIND[0] * 0.22, oy = -WIND[1] * 0.22;
      ctx.beginPath(); ctx.moveTo(x0 + ox, y0 + oy); ctx.quadraticCurveTo(mx + ox, my + oy, x1 + ox, y1 + oy); ctx.stroke();
    }
    ctx.restore();
    // 波光：在屏幕坐标里画（保证放大缩小时都是细小的亮点），加法混合
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalCompositeOperation = 'lighter';
    for (const g of glints) {
      const ph = (g.t % 1), f = Math.pow(Math.max(0, Math.sin(ph * Math.PI)), 10) * g.peak * glintK;   // 一闪即逝
      if (f < 0.03) continue;
      const p = toImg(g.x, g.y, WZ), sx = O.x + p[0] * sc, sy = O.y + p[1] * sc;
      const r = Math.max(0.9, g.size * px) * (0.6 + 0.6 * f);
      const grd = ctx.createRadialGradient(sx, sy, 0, sx, sy, r * 2.2);
      const g1 = Math.round(252 - 40 * warm), b1 = Math.round(236 - 110 * warm);           // 低太阳时波光偏金色
      grd.addColorStop(0, `rgba(255,${g1},${b1},${0.95 * f})`); grd.addColorStop(0.35, `rgba(255,${g1 - 6},${b1 - 20},${0.45 * f})`); grd.addColorStop(1, 'rgba(255,230,180,0)');
      ctx.fillStyle = grd; ctx.beginPath(); ctx.ellipse(sx, sy, r * 2.2, r * 1.1, 0, 0, TAU); ctx.fill();
      if (f > 0.55 && r > 1.6) {                                    // 最亮时的十字星芒
        ctx.strokeStyle = `rgba(255,252,240,${0.5 * f})`; ctx.lineWidth = 0.8;
        ctx.beginPath(); ctx.moveTo(sx - r * 3, sy); ctx.lineTo(sx + r * 3, sy); ctx.moveTo(sx, sy - r * 1.8); ctx.lineTo(sx, sy + r * 1.8); ctx.stroke();
      }
    }
    // 夜里：路灯倒影——竖向的一串碎光，随水波晃动、明灭
    if (lampK > 0.02) {
      if (!lampVisChecked && vis) { for (let i = lampRefl.length - 1; i >= 0; i--) if (!visible(lampRefl[i].wx, lampRefl[i].wy)) lampRefl.splice(i, 1); lampVisChecked = true; }
      const now = performance.now() / 1000;
      for (const L of lampRefl) {
        const p = toImg(L.x, L.y, 2 * WZ - L.z), sx = O.x + p[0] * sc, sy = O.y + p[1] * sc, u = Math.max(1.2, 0.5 * px);
        for (let i = 0; i < 7; i++) {
          const fl = 0.5 + 0.5 * Math.sin(now * 3.1 * L.sp + L.ph + i * 1.7) * Math.sin(now * 1.3 + i);
          const a = lampK * (0.75 - i * 0.09) * (0.35 + 0.65 * fl); if (a <= 0.02) continue;
          const wdt = u * (1.4 + 1.6 * fl) * (1 - i * 0.08), yy = sy - u * 1.5 + i * u * 1.3, xx = sx + Math.sin(now * 2 + i * 2.3 + L.ph) * u * 0.6;
          ctx.fillStyle = `rgba(255,${200 + 30 * fl | 0},${120 + 40 * fl | 0},${a})`;
          ctx.fillRect(xx - wdt, yy, wdt * 2, Math.max(0.8, u * 0.45));
        }
      }
    }
    ctx.globalCompositeOperation = 'source-over';
  }

  let last = performance.now(), playing = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function tick(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (!glints.length) fill();
    if (playing) step(dt);
    if (!zooming) draw();
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
  window.WT = { toggle() { playing = !playing; return playing; }, get playing() { return playing; }, glints, waves, simulate(s, dt = 0.05) { if (!glints.length) fill(); for (let t = 0; t < s; t += dt) step(dt); } };
})();
