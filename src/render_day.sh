#!/bin/bash
# 一天 11 个时刻的正式全图（含精细外立面、门前整片铺装、路灯与夜景灯光）+ 点击轮廓图 + 可见性图
# 用法：bash ~/Desktop/SUIBE-地图/suibe-render/render_day.sh        （约 1 小时，可以去做别的事）
cd "$(dirname "$0")"
BL=""
for c in "/Applications/Blender.app/Contents/MacOS/Blender" "$HOME/Applications/Blender.app/Contents/MacOS/Blender"; do
  [ -x "$c" ] && BL="$c" && break
done
[ -z "$BL" ] && { echo "❌ 没找到 Blender"; exit 1; }
mkdir -p logs renders/day
run () {   # run <名称> <环境变量...> -- <参数...>
  name=$1; shift; envs=(); while [ "$1" != "--" ]; do envs+=("$1"); shift; done; shift
  if [ -f "renders/day/$name.done" ]; then echo "⏭  $name 已完成，跳过"; return; fi
  t0=$(date +%s); echo "⏳ [$(date +%H:%M:%S)] $name ..."
  env "${envs[@]}" "$BL" -b --factory-startup --python-exit-code 1 -P suibe.py -- "$@" > "logs/$name.log" 2>&1 \
    || { echo "❌ $name 失败，见 logs/$name.log"; tail -25 "logs/$name.log"; exit 1; }
  touch "renders/day/$name.done"; echo "✅ $name 完成，用时 $(( $(date +%s) - t0 )) 秒"
}
for T in 12:00 05:20 06:15 08:00 10:00 14:30 16:30 17:30 18:10 20:00 02:00; do
  N="day_${T/:/}"
  run "$N" TIME=$T HQ=1 DETAIL=1 NO_AGENTS=1 OUT_NAME=day/$N -- scene 6000 4500 128
  cp renders/meta_6000.json "renders/day/meta_${T/:/}.json"
done
run mask NO_AGENTS=1 DETAIL=1 -- mask 6000 4500 1
run vis NO_AGENTS=1 DETAIL=1 -- vis 6000 4500 1
echo "🎉 全部完成！结果在 $(pwd)/renders/day 以及 renders/mask_6000.png、vis_6000.png"
