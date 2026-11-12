#!/bin/bash
# 上海对外经贸大学图书馆环境地图 · Mac 本地渲染（Apple 芯片 Metal GPU）
# 用法：在"终端"里运行   bash ~/Downloads/suibe-render/render_mac.sh（先跑测试：末尾加 test）
cd "$(dirname "$0")"
BL=""
for c in "/Applications/Blender.app/Contents/MacOS/Blender" "$HOME/Applications/Blender.app/Contents/MacOS/Blender"; do
  [ -x "$c" ] && BL="$c" && break
done
if [ -z "$BL" ]; then echo "❌ 没找到 Blender：请从 blender.org 下载 macOS Apple Silicon 版，拖进「应用程序」"; exit 1; fi
echo "✅ $("$BL" --version 2>/dev/null | head -1)"
mkdir -p renders logs
if [ ! -d pylibs/shapely ]; then
  echo "⏳ 首次运行：给 Blender 安装 shapely（几何库）..."
  "$BL" -b --factory-startup --python-exit-code 1 --python-expr "import sys,subprocess; subprocess.call([sys.executable,'-m','ensurepip','--upgrade']); subprocess.check_call([sys.executable,'-m','pip','install','--no-deps','--target','pylibs','shapely'])" > logs/install.log 2>&1 \
    || { echo "❌ 安装失败，见 logs/install.log"; tail -15 logs/install.log; exit 1; }
fi
run () {   # run <名称> <环境变量...> -- <stage> <宽> <高> <采样>
  name=$1; shift; envs=(); while [ "$1" != "--" ]; do envs+=("$1"); shift; done; shift
  t0=$(date +%s); echo "⏳ [$(date +%H:%M:%S)] $name ..."
  env "${envs[@]}" "$BL" -b --factory-startup --python-exit-code 1 -P suibe.py -- "$@" > "logs/$name.log" 2>&1 \
    || { echo "❌ $name 失败，见 logs/$name.log"; tail -25 "logs/$name.log"; exit 1; }
  echo "✅ $name 完成，用时 $(( $(date +%s) - t0 )) 秒   $(grep -m1 '^GPU:' logs/$name.log)"
}
MODE=${1:-full}
if [ "$MODE" = "test" ] || [ ! -f renders/test_ok ]; then
  run 测试小图 HQ=1 NO_AGENTS=1 -- preview 1200 900 16 && mv renders/scene_1200.png renders/test_1200.png && touch renders/test_ok
  [ "$MODE" = "test" ] && { echo "🎉 测试通过：renders/test_1200.png"; exit 0; }
fi
run 正式全图 HQ=1 NO_AGENTS=1 -- scene 6000 4500 128
run 点击轮廓图 NO_AGENTS=1 -- mask 6000 4500 1
run 可见性图 NO_AGENTS=1 -- vis 6000 4500 1
echo "🎉 全部完成！结果在 $(pwd)/renders ：scene_6000.png  mask_6000.png  vis_6000.png  meta_6000.json"
open renders 2>/dev/null
