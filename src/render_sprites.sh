#!/bin/bash
# 精细版动画素材（白鹭 + 学生 动作帧）· Mac 本地渲染（Metal GPU）
# 用法：bash ~/Desktop/SUIBE-地图/suibe-render/render_sprites.sh
cd "$(dirname "$0")"
BL=""
for c in "/Applications/Blender.app/Contents/MacOS/Blender" "$HOME/Applications/Blender.app/Contents/MacOS/Blender"; do
  [ -x "$c" ] && BL="$c" && break
done
[ -z "$BL" ] && { echo "❌ 没找到 Blender"; exit 1; }
[ -d pylibs/shapely ] || { echo "❌ 请先运行一次 render_mac.sh（会安装 shapely）"; exit 1; }
mkdir -p logs sprites
for part in egrets students; do
  t0=$(date +%s); echo "⏳ [$(date +%H:%M:%S)] 渲染 $part 动作帧 ..."
  HQ=1 "$BL" -b --factory-startup --python-exit-code 1 -P sprites2.py -- --$part > "logs/sprites_$part.log" 2>&1 \
    || { echo "❌ 失败，见 logs/sprites_$part.log"; tail -25 "logs/sprites_$part.log"; exit 1; }
  echo "✅ $part 完成，用时 $(( $(date +%s) - t0 )) 秒   $(grep -m1 '^GPU:' logs/sprites_$part.log)"
done
echo "🎉 全部完成！结果在 $(pwd)/sprites ：egrets2.png  egrets2_refl.png  students2.png  sprites2.json"
open sprites 2>/dev/null
