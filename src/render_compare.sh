#!/bin/bash
# 图书馆外立面 改前 / 改后 局部对比（不影响正式全图）
# 用法：bash ~/Desktop/SUIBE-地图/suibe-render/render_compare.sh
cd "$(dirname "$0")"
BL=""
for c in "/Applications/Blender.app/Contents/MacOS/Blender" "$HOME/Applications/Blender.app/Contents/MacOS/Blender"; do
  [ -x "$c" ] && BL="$c" && break
done
[ -z "$BL" ] && { echo "❌ 没找到 Blender"; exit 1; }
mkdir -p logs renders
BORDER="0.3648,0.5205,0.5832,0.7725"     # 图书馆北立面 + 拱门入口 + 湖东南角
for d in 0 1; do
  t0=$(date +%s); echo "⏳ [$(date +%H:%M:%S)] 渲染 $([ $d = 1 ] && echo 改后 || echo 改前) ..."
  DETAIL=$d HQ=1 NO_AGENTS=1 OUT_NAME=cmp_detail$d "$BL" -b --factory-startup --python-exit-code 1 -P suibe.py -- preview 6000 4500 96 $BORDER > logs/cmp_detail$d.log 2>&1 \
    || { echo "❌ 失败，见 logs/cmp_detail$d.log"; tail -25 logs/cmp_detail$d.log; exit 1; }
  echo "✅ 完成，用时 $(( $(date +%s) - t0 )) 秒"
done
echo "🎉 对比图完成：renders/cmp_detail0.png（改前）  renders/cmp_detail1.png（改后）"
