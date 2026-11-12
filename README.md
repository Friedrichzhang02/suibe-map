# 上海对外经贸大学 · 松江校区三维环境地图（个人项目）

仿照 [UofT Map](https://map.utoronto.ca) 的思路，做一张可缩放、可点击、带动画和 24 小时光影的校园三维地图。
当前完成的是第一个片区：**图书馆与思源湖**（v0.1 原型）。

> 个人兴趣项目，非学校官方地图。建筑立面与周边建筑为示意。

## 在线查看

开启 GitHub Pages（Settings → Pages → Branch: `main` / 文件夹 `/docs`）后，访问：

`https://Friedrichzhang02.github.io/suibe-map`

## 已实现

- 等距视角三维渲染底图（Blender + Cycles），可缩放拖动，点击图书馆 / 思源湖查看信息
- 24 小时光影：11 个时刻（春分日真实太阳轨迹），时间条拖动 / 播放 / 跟随上海当前时间
- 夜景：窗户随机亮灯、约 180 盏路灯、湖面灯光倒影
- 实时动画：4 只白鹭（盘旋、滑翔、降落、涉水觅食、水中倒影），约 22 名学生（散步、交谈、看手机、坐下看书、进出图书馆）
- 湖面波光粼粼、顺风细浪

## 仓库结构

```
docs/      已生成的网页（GitHub Pages 直接发布这个文件夹）
src/       生成网页的全部源文件
  suibe.py         场景脚本（"图纸"）：地形、建筑、道路、树、路灯、灯光、相机
  trees2.py        树木模型（香樟、柳树、水杉等）
  sprites2.py      白鹭和学生的动作帧（精灵图）
  render_*.sh      在 Mac 上用 Blender（Metal GPU）渲染的一键脚本
  postprocess.py   渲染图 → 点击轮廓、白鹭落点、数据
  day_tiles.py     每个时刻的大图 → 1024px 瓦片
  build_page.py    模板 + 数据 → 网页
  page_template.html, egrets.js, students.js, water.js   网页与动画
  vendor/          Leaflet 地图库
DEVELOPMENT.md   开发方式与流程（后续按这个推进）
```

## 生成流程（简版）

1. Mac 上安装 Blender 5.x，运行 `bash src/render_mac.sh test` 检查环境
2. `bash src/render_day.sh` → 11 个时刻的全图 + 点击轮廓图 + 可见性图（M3 Pro 约 40–60 分钟）
3. `bash src/render_sprites.sh` → 白鹭和学生的动作帧（几分钟）
4. 后处理与打包：`postprocess.py` → `day_tiles.py` → `build_page.py`，输出到 `src/site/`，再复制到 `docs/`

渲染的大图（每张 30–40 MB）不放进仓库，需要时重新渲染即可。

## 数据来源与版权

- 道路、湖岸等矢量数据：© [OpenStreetMap](https://www.openstreetmap.org/copyright) contributors（ODbL）
- 卫星影像只在制作时用于量取位置与尺寸，**不包含在本仓库和网页中**
- 地图库：[Leaflet](https://leafletjs.com)（BSD-2-Clause）
