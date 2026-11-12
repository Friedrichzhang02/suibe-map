/*
 * 学生动画层：沿步道网络行走，散步 / 坐长椅或台阶 / 停下交谈 / 进出图书馆北门 / 走出、走进画面。
 * 与白鹭共用投影矩阵（世界坐标米 → 底图像素），被树或建筑挡住时按可见性图渐隐。
 */
(function () {
  const A = DATA.anim, SP = SPRITES.students2;
  const MAXZ = window.EG_MAXZ, PXM_IMG = window.EG_PXM, SPR_SCALE = PXM_IMG / SP.pxm;
  const P = A.proj, SUN = A.sun, W = DATA.w, H = DATA.h;
  const TAU = Math.PI * 2, rand = (a, b) => a + Math.random() * (b - a), pick = a => a[Math.floor(Math.random() * a.length)];
  const TARGET = 22;                                // 同屏人数
  const COL = Object.fromEntries(SP.cols.map((k, i) => [k, i]));

  // ---------------------------------------------------------------- 画布
  const map = window.EG_MAP;
  const cv = document.createElement('canvas');
  cv.style.cssText = 'position:absolute;inset:0;pointer-events:none;z-index:449';
  map.getContainer().appendChild(cv);
  const ctx = cv.getContext('2d');
  const sheet = new Image(); sheet.src = 'sprites/students2.webp';
  let dpr = 1;
  function resize() { dpr = Math.min(window.devicePixelRatio || 1, 2); const s = map.getSize(); cv.width = s.x * dpr; cv.height = s.y * dpr; cv.style.width = s.x + 'px'; cv.style.height = s.y + 'px'; }
  resize(); map.on('resize', resize);
  let zooming = false;
  map.on('zoomanim', () => { zooming = true; cv.style.opacity = 0; });
  map.on('zoomend', () => { zooming = false; cv.style.transition = 'opacity .25s'; cv.style.opacity = 1; });
  const toImg = (x, y, z) => [P[0][0] + x * P[1][0] + y * P[2][0] + z * P[3][0], P[0][1] + x * P[1][1] + y * P[2][1] + z * P[3][1]];

  // 可见性图（地面 = 白）：判断脚下是否被树 / 建筑挡住
  let visData = null, visW = 0, visH = 0;
  const vimg = new Image(); vimg.src = 'sprites/vis.png';
  vimg.onload = () => { const c = document.createElement('canvas'); visW = c.width = vimg.width; visH = c.height = vimg.height; const g = c.getContext('2d'); g.drawImage(vimg, 0, 0); visData = g.getImageData(0, 0, visW, visH).data; };
  function visible(x, y, z) {
    if (!visData) return true;
    const p = toImg(x, y, z || 0), u = Math.round(p[0] * visW / W), v = Math.round(p[1] * visH / H);
    if (u < 0 || v < 0 || u >= visW || v >= visH) return false;
    return visData[(v * visW + u) * 4] > 127;
  }
  const inImage = (x, y) => { const p = toImg(x, y, 0); return p[0] > 30 && p[1] > 30 && p[0] < W - 30 && p[1] < H - 30; };

  // ---------------------------------------------------------------- 步道网络
  const nodes = [], adj = [];
  function addNode(x, y, z = 0, tag) { nodes.push({x, y, z, tag}); adj.push([]); return nodes.length - 1; }
  function link(a, b) { const d = Math.hypot(nodes[a].x - nodes[b].x, nodes[a].y - nodes[b].y); adj[a].push([b, d]); adj[b].push([a, d]); }
  const lineOf = [];
  for (const [name, pts] of Object.entries(A.walks)) {
    let prev = null;
    for (let i = 0; i < pts.length - 1; i++) {
      const [x0, y0, z0 = 0] = pts[i], [x1, y1, z1 = 0] = pts[i + 1], L = Math.hypot(x1 - x0, y1 - y0), k = Math.max(1, Math.ceil(L / 6));
      for (let j = (i === 0 ? 0 : 1); j <= k; j++) {
        const t = j / k, id = addNode(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t, z0 + (z1 - z0) * t, name); lineOf[id] = name;
        if (prev !== null) link(prev, id); prev = id;
      }
    }
  }
  // 不同步道之间相距 < 5 m 的点视为路口
  const grid = new Map(), key = (x, y) => `${Math.floor(x / 5)},${Math.floor(y / 5)}`;
  nodes.forEach((n, i) => { const k = key(n.x, n.y); if (!grid.has(k)) grid.set(k, []); grid.get(k).push(i); });
  function near(x, y, r) {
    const out = []; const gx = Math.floor(x / 5), gy = Math.floor(y / 5);
    for (let i = -1; i <= 1; i++) for (let j = -1; j <= 1; j++) for (const id of grid.get(`${gx + i},${gy + j}`) || [])
      if (Math.hypot(nodes[id].x - x, nodes[id].y - y) < r) out.push(id);
    return out;
  }
  const baseCount = nodes.length;
  for (let i = 0; i < baseCount; i++) for (const j of near(nodes[i].x, nodes[i].y, 5)) if (j > i && lineOf[j] !== lineOf[i] && Math.abs(nodes[i].z - nodes[j].z) < 0.2) link(i, j);
  function nearestNode(x, y, z) {
    let b = -1, bd = 1e9;
    for (let i = 0; i < baseCount; i++) { const d = Math.hypot(nodes[i].x - x, nodes[i].y - y) + (z === undefined ? 0 : Math.abs(nodes[i].z - z) * 20); if (d < bd) { bd = d; b = i; } }
    return b;
  }
  // 座位：长椅（每张 2 个位置）+ 台阶
  const seats = [];
  for (const [x, y, face, bz = 0] of A.benches) {
    const ax = -Math.sin(face), ay = Math.cos(face);           // 长椅长轴
    for (const s of [-0.45, 0.45]) {
      const sx = x + ax * s, sy = y + ay * s, front = addNode(sx + Math.cos(face) * 0.9, sy + Math.sin(face) * 0.9, bz, 'seatfront');
      link(front, nearestNode(sx, sy, bz));
      seats.push({x: sx, y: sy, z: bz + 0.06, face, front, busy: null, vis: visible(sx, sy, bz)});
    }
  }
  for (const [x, y, z, face] of A.terraceSeats) {
    const front = nearestNode(x, y, A.ringZ || 0);
    seats.push({x, y, z: z - 0.47, face, front, busy: null, vis: visible(x, y, z), step: true});
  }
  const goodSeats = seats.filter(s => s.vis && inImage(s.x, s.y));
  // 图书馆北门
  const doorIn = addNode(A.door[0], A.door[1], 0, 'door'), doorOut = addNode(A.doorOut[0], A.doorOut[1], 0, 'doorOut');
  link(doorIn, doorOut); link(doorOut, nearestNode(A.doorOut[0], A.doorOut[1] + 2, 0));
  // 画面外的出入口：步道 / 人行道上投影到画面外的端点
  const exits = [];
  for (let i = 0; i < baseCount; i++) if (!inImage(nodes[i].x, nodes[i].y) && adj[i].some(([j]) => inImage(nodes[j].x, nodes[j].y))) exits.push(i);
  // 散步目的地：画面内、可见、在湖边步道或南岸步道上
  const strollTargets = [];
  for (let i = 0; i < baseCount; i++) if (inImage(nodes[i].x, nodes[i].y) && visible(nodes[i].x, nodes[i].y) && (lineOf[i] === 'prom' || /^path/.test(lineOf[i]))) strollTargets.push(i);

  function route(a, b) {                       // Dijkstra（二叉堆）
    const N = nodes.length, dist = new Float64Array(N).fill(Infinity), prev = new Int32Array(N).fill(-1), done = new Uint8Array(N);
    const heap = [[0, a]]; dist[a] = 0;
    const push = e => { heap.push(e); let i = heap.length - 1; while (i > 0) { const p = (i - 1) >> 1; if (heap[p][0] <= heap[i][0]) break; [heap[p], heap[i]] = [heap[i], heap[p]]; i = p; } };
    const pop = () => { const top = heap[0], last = heap.pop(); if (heap.length) { heap[0] = last; let i = 0; for (;;) { const l = 2 * i + 1, r = l + 1; let m = i; if (l < heap.length && heap[l][0] < heap[m][0]) m = l; if (r < heap.length && heap[r][0] < heap[m][0]) m = r; if (m === i) break; [heap[m], heap[i]] = [heap[i], heap[m]]; i = m; } } return top; };
    while (heap.length) {
      const [d, u] = pop(); if (done[u]) continue; done[u] = 1; if (u === b) break;
      for (const [v, w] of adj[u]) if (d + w < dist[v]) { dist[v] = d + w; prev[v] = u; push([dist[v], v]); }
    }
    if (!isFinite(dist[b])) return null;
    const path = []; for (let v = b; v !== -1; v = prev[v]) path.push(v); return path.reverse();
  }

  // ---------------------------------------------------------------- 学生
  const people = [];
  class Student {
    constructor(at, opts = {}) {
      const n = nodes[at];
      this.variant = Math.floor(Math.random() * SP.variants);
      this.x = n.x; this.y = n.y; this.z = n.z; this.h = rand(0, TAU); this.node = at;
      this.speed = rand(1.0, 1.45); this.lane = rand(-0.9, 0.9); this.dist = 0;
      this.state = 'idle'; this.timer = 0; this.alpha = opts.alpha ?? 0; this.fadeIn = opts.fadeIn ?? true;
      this.path = null; this.goal = null; this.seat = null; this.partner = null; this.gone = false; this.cooldown = rand(5, 20);
    }
    go(target, then) {
      const r = route(this.node, target);
      if (!r || r.length < 2) { this.state = 'idle'; return false; }
      this.path = r; this.pi = 1; this.state = 'walk'; this.then = then; return true;
    }
    choose() {
      const r = Math.random(), byDist = (arr, f) => arr.slice().sort((a, b) => f(a) - f(b));
      const dd = (x, y) => Math.hypot(x - this.x, y - this.y);
      if (r < 0.34 && goodSeats.length) {              // 去附近的座位坐一会儿
        const free = byDist(goodSeats.filter(s => !s.busy), s => dd(s.x, s.y)).slice(0, 6);
        if (free.length) { const s = pick(free); s.busy = this; this.seat = s; if (this.go(s.front, 'sit')) return; s.busy = null; this.seat = null; }
      }
      if (r < 0.48) { if (this.go(doorIn, 'enter')) return; }                       // 进图书馆
      if (r < 0.62 && exits.length) { if (this.go(pick(byDist(exits, i => dd(nodes[i].x, nodes[i].y)).slice(0, 4)), 'leave')) return; }   // 走出画面
      const near = byDist(strollTargets, i => Math.abs(dd(nodes[i].x, nodes[i].y) - rand(30, 90))).slice(0, 12);   // 散步 30–90 m
      this.go(pick(near), 'stroll');
    }
    update(dt) {
      this.cooldown -= dt;
      if (this.state === 'gone') return;
      if (this.state === 'idle') { this.choose(); return; }
      if (this.state === 'walk') {
        const n = nodes[this.path[this.pi]];
        const lane = (n.tag === 'door' || n.tag === 'doorOut' || n.tag === 'seatfront') ? 0 : this.lane;
        const a = nodes[this.path[this.pi - 1]], sx = n.x - a.x, sy = n.y - a.y, L = Math.hypot(sx, sy) || 1;
        const tx = n.x - sy / L * lane, ty = n.y + sx / L * lane;
        const dx = tx - this.x, dy = ty - this.y, d = Math.hypot(dx, dy), step = this.speed * dt;
        if (d > 1e-3) this.h = Math.atan2(dy, dx);
        this.z = n.z + (a.z - n.z) * Math.min(1, d / L);
        if (d <= step) { this.x = tx; this.y = ty; this.z = n.z; this.node = this.path[this.pi]; this.pi++; if (this.pi >= this.path.length) this.arrive(); }
        else { this.x += dx / d * step; this.y += dy / d * step; }
        this.dist += Math.min(d, step);
        // 进门时淡出
        if (this.then === 'enter' && this.path[this.pi] === doorIn) this.alpha = Math.min(this.alpha, Math.max(0, Math.hypot(this.x - nodes[doorIn].x, this.y - nodes[doorIn].y) / 4));
        // 偶遇：两人相距 < 3 m、都在散步，可能停下交谈
        if (this.then === 'stroll' && this.cooldown < 0 && Math.random() < dt * 1.2) {
          for (const o of people) if (o !== this && o.state === 'walk' && o.then === 'stroll' && o.cooldown < 0 && Math.hypot(o.x - this.x, o.y - this.y) < 4.5) {
            const T = rand(10, 24); for (const p of [this, o]) { p.state = 'talk'; p.timer = T; p.cooldown = 40; }
            this.partner = o; o.partner = this;
            const ang = Math.atan2(o.y - this.y, o.x - this.x); this.h = ang; o.h = ang + Math.PI; break;
          }
        }
        return;
      }
      if (this.state === 'moveTo') {
        const m = this.move, dx = m.x - this.x, dy = m.y - this.y, d = Math.hypot(dx, dy), step = 0.8 * dt;
        if (d > 1e-3) this.h = Math.atan2(dy, dx);
        if (d <= step) { this.x = m.x; this.y = m.y; m.done(); } else { this.x += dx / d * step; this.y += dy / d * step; this.dist += step; }
        return;
      }
      if (this.state === 'talk') {
        this.timer -= dt; this.talkT = (this.talkT || 0) + dt;
        if (this.timer <= 0) { this.state = 'idle'; this.partner = null; this.solo = false; }
        return;
      }
      if (this.state === 'sitting') {
        this.timer -= dt;
        if (this.timer <= 0) {           // 起身，走回步道
          const s = this.seat, f = nodes[s.front]; this.z = f.z; s.busy = null; this.seat = null;
          this.state = 'moveTo'; this.move = {x: f.x, y: f.y, done: () => { this.node = s.front; this.state = 'idle'; }};
        }
        return;
      }
    }
    arrive() {
      if (this.then === 'sit') {          // 走到座位前，再坐下
        const s = this.seat; this.state = 'moveTo';
        this.move = {x: s.x, y: s.y, done: () => { this.state = 'sitting'; this.z = s.z; this.h = s.face; this.timer = rand(20, 60); this.sitPose = Math.random() < 0.35 ? 'read' : 'sit'; }};
        this.z = nodes[s.front].z;
        return;
      }
      if (this.then === 'enter' || this.then === 'leave') { this.gone = true; this.state = 'gone'; return; }
      if (Math.random() < 0.3) { this.state = 'talk'; this.timer = rand(3, 8); this.solo = true; this.soloPose = Math.random() < 0.5 ? 'phone' : 'stand'; return; }   // 停下看看湖 / 看手机
      this.state = 'idle';
    }
    pose() {
      if (this.state === 'walk' || this.state === 'moveTo') return 'walk' + (Math.floor(this.dist / 0.18) % SP.walk);
      if (this.state === 'sitting') return this.sitPose || 'sit';
      if (this.state === 'talk') {
        if (this.solo) return this.soloPose || 'stand';
        const g = Math.floor(this.talkT * 1.3 + this.variant) % 4;                 // 说话时手势交替
        return g === 0 ? 'talk1' : g === 2 ? 'talk2' : 'stand';
      }
      return 'stand';
    }
  }

  // 生成：画面边缘走进来，或从图书馆走出来
  function spawn(initial) {
    let s;
    if (initial) {
      const at = pick(strollTargets); s = new Student(at, {alpha: 1});
      if (Math.random() < 0.35 && goodSeats.length) {          // 一开始就有人坐着
        const free = goodSeats.filter(q => !q.busy); if (free.length) { const q = pick(free); q.busy = s; s.seat = q; s.state = 'sitting'; s.x = q.x; s.y = q.y; s.z = q.z; s.h = q.face; s.timer = rand(10, 50); s.sitPose = Math.random() < 0.35 ? 'read' : 'sit'; }
      }
    } else if (Math.random() < 0.45) { s = new Student(doorIn, {alpha: 0}); s.go(doorOut, 'stroll'); s.path.push(...(route(doorOut, pick(strollTargets)) || []).slice(1)); }
    else { s = new Student(pick(exits), {alpha: 1}); }
    people.push(s); return s;
  }
  for (let i = 0; i < TARGET - 5; i++) spawn(true);
  // 开场：两组人正在广场上聊天（北入口外、粉色广场）
  for (const [cx, cy, n, cz] of [[A.doorOut[0] - 3, A.doorOut[1] + 3, 3, 0], [A.pinkPlaza[0], A.pinkPlaza[1] + 2, 2, A.ringZ || 0]]) {
    const at = nearestNode(cx, cy, cz), grp = [];
    for (let i = 0; i < n; i++) {
      const s = new Student(at, {alpha: 1}), a = i / n * TAU;
      s.x = cx + Math.cos(a) * 0.75; s.y = cy + Math.sin(a) * 0.75; s.z = cz; s.h = a + Math.PI; s.state = 'talk'; s.timer = rand(15, 30); s.cooldown = 40;
      people.push(s); grp.push(s);
    }
  }
  // 两两结伴
  for (let i = 0; i < 4; i++) { const a = people[i * 2], b = people[i * 2 + 1]; if (a.state !== 'sitting' && b.state !== 'sitting') { b.x = a.x + 0.8; b.y = a.y + 0.3; b.node = a.node; } }

  // ---------------------------------------------------------------- 绘制
  const cell = SP.cell, anc = SP.anchor;
  let O, sc;
  function draw(dt) {
    O = map.latLngToContainerPoint([0, 0]); sc = Math.pow(2, map.getZoom() - MAXZ);
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, cv.width, cv.height);
    const k = SPR_SCALE * sc;
    const list = people.filter(p => p.alpha > 0.01).map(p => [p, toImg(p.x, p.y, p.z)]).sort((a, b) => a[1][1] - b[1][1]);
    // 影子
    ctx.setTransform(dpr * sc * P[1][0], dpr * sc * P[1][1], dpr * sc * P[2][0], dpr * sc * P[2][1], dpr * (O.x + sc * P[0][0]), dpr * (O.y + sc * P[0][1]));
    ctx.fillStyle = '#10180c';
    const SUN = window.TOD ? TOD.sun : A.sun, SH = window.TOD ? TOD.shadow : 1;
    for (const [p] of (SH > 0.02 ? list : [])) {
      const Hh = (p.state === 'sitting' ? 1.1 : 1.7) * A.scale.people, f = Hh / SUN[2], dx = -SUN[0] * f, dy = -SUN[1] * f;
      const fz = p.state === 'sitting' ? p.z + 0.47 : p.z;
      ctx.globalAlpha = 0.22 * p.alpha * SH * nightVis(p);
      ctx.save();
      const sh = toImg(0, 0, fz);
      ctx.setTransform(dpr * sc * P[1][0], dpr * sc * P[1][1], dpr * sc * P[2][0], dpr * sc * P[2][1], dpr * (O.x + sc * sh[0]), dpr * (O.y + sc * sh[1]));
      ctx.beginPath(); ctx.ellipse(p.x + dx / 2, p.y + dy / 2, Math.hypot(dx, dy) / 2, 0.22 * A.scale.people, Math.atan2(dy, dx), 0, TAU); ctx.fill();
      ctx.restore();
    }
    ctx.globalAlpha = 1; ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.filter = window.TOD ? TOD.filter : 'none';
    for (const [p, ip] of list) {
      const hi = ((Math.round(p.h / TAU * SP.headings) % SP.headings) + SP.headings) % SP.headings;
      const row = p.variant * SP.headings + hi, col = COL[p.pose()];
      const sx = O.x + ip[0] * sc, sy = O.y + ip[1] * sc;
      ctx.globalAlpha = p.alpha * nightVis(p); if (ctx.globalAlpha < 0.01) continue;
      ctx.drawImage(sheet, col * cell, row * cell, cell, cell, sx - anc[0] * k, sy - anc[1] * k, cell * k, cell * k);
    }
    ctx.globalAlpha = 1; ctx.filter = 'none';
  }
  // 夜里人少：只留约三分之一的人（主要是进出图书馆、坐着的）
  function nightVis(p) { if (!window.TOD || TOD.night < 0.01) return 1; return (p.variant % 3 === 0 || p.state === 'sitting') ? 1 : 1 - TOD.night; }
  function stepAll(dt) {
    for (const p of people) {
      p.update(dt);
      // 透明度：脚下被遮挡 → 淡出；刚生成 → 淡入
      const target = p.gone ? 0 : (visible(p.x, p.y, p.z + (p.state === 'sitting' ? 0.47 : 0)) ? 1 : 0);
      if (!(p.then === 'enter' && p.path && p.path[p.pi] === doorIn)) p.alpha += (target - p.alpha) * Math.min(1, dt * 4);
    }
    for (let i = people.length - 1; i >= 0; i--) if (people[i].gone && people[i].alpha < 0.05) { const s = people[i]; if (s.seat) s.seat.busy = null; people.splice(i, 1); }
    while (people.length < TARGET) spawn(false);
  }
  let last = performance.now(), playing = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  function tick(now) {
    const dt = Math.min(0.05, (now - last) / 1000); last = now;
    if (playing) stepAll(dt);
    if (!zooming && sheet.complete) draw(dt);
    requestAnimationFrame(tick);
  }
  requestAnimationFrame(tick);
  window.ST = {
    people, nodes, exits, seats: goodSeats,
    toggle() { playing = !playing; return playing; }, get playing() { return playing; },
    simulate(seconds, dt = 0.05) { for (let t = 0; t < seconds; t += dt) stepAll(dt); },
    pos(i) { const p = people[i]; return toImg(p.x, p.y, p.z); },
  };
})();
