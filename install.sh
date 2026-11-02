#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# Elench 一键安装脚本（Linux / macOS）
#
#   curl -fsSL https://raw.githubusercontent.com/xiaohuo123212/elench/main/install.sh | bash
#   # 或者在仓库里：
#   ./install.sh              # 安装
#   ./install.sh --dry-run    # 只打印将要做的事
#   ./install.sh --uninstall  # 卸载
#
# 做两件事，按可用性二选一：
#   ① 有 pip  → `python3 -m pip install --user .`，装成真正的控制台命令
#   ② 没 pip  → 在 ~/.local/bin 放一个启动器，指向本仓库的 src/elench.py
#
# **不需要 sudo，不写系统目录，不动系统 PATH**，只动 `~/.local/bin` 与 shell 配置。
# ---------------------------------------------------------------------------
set -eu

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd || pwd)"
BIN_DIR="${HOME}/.local/bin"
DRY=0
UNINSTALL=0

for arg in "$@"; do
  case "$arg" in
    --dry-run) DRY=1 ;;
    --uninstall) UNINSTALL=1 ;;
    -h|--help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0 ;;
    *) echo "[elench] 未知参数：$arg" >&2; exit 2 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
run()  { if [ "$DRY" -eq 1 ]; then printf '  [dry-run] %s\n' "$*"; else eval "$@"; fi; }

find_python() {
  for c in python3 python; do
    if command -v "$c" >/dev/null 2>&1; then
      if "$c" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
        echo "$c"; return 0
      fi
    fi
  done
  return 1
}

# ---------------------------------------------------------------- 卸载
if [ "$UNINSTALL" -eq 1 ]; then
  say "卸载 elench（只动用户级）"
  run "rm -f '$BIN_DIR/elench'"
  if command -v python3 >/dev/null 2>&1; then
    run "python3 -m pip uninstall -y elench >/dev/null 2>&1 || true"
  fi
  say "已删除 $BIN_DIR/elench（shell 配置里的 PATH 行可手动删）"
  exit 0
fi

# ---------------------------------------------------------------- 安装
say "Elench 安装器（Linux/macOS）"
say "仓库目录：$REPO_DIR"

PY="$(find_python || true)"
if [ -z "$PY" ]; then
  say "✗ 找不到 Python 3.10+。先装："
  say "    Debian/Ubuntu : sudo apt install python3 python3-pip"
  say "    Fedora        : sudo dnf install python3 python3-pip"
  say "    macOS         : brew install python@3.12"
  exit 1
fi
say "Python：$PY（$("$PY" --version 2>&1)）"

mkdir -p "$BIN_DIR"
say "用户级命令目录：$BIN_DIR"

method=""
if "$PY" -m pip --version >/dev/null 2>&1 && [ -f "$REPO_DIR/pyproject.toml" ]; then
  method="pip"
else
  method="launcher"
fi
say "安装方式：$method"

if [ "$method" = "pip" ]; then
  # --user 装到 ~/.local；--no-build-isolation 避免为了构建去联网拉 setuptools
  run "'$PY' -m pip install --user --upgrade --no-build-isolation '$REPO_DIR'"
else
  say "没有可用的 pip，改写启动器（指向仓库源码）"
  LAUNCHER="$BIN_DIR/elench"
  run "cat > '$LAUNCHER' <<'SH'
#!/usr/bin/env bash
exec '$PY' '$REPO_DIR/src/elench.py' \"\$@\"
SH"
  run "chmod +x '$LAUNCHER'"
fi

# ---------------------------------------------------------------- PATH
add_path_line() {
  local rc="$1"
  [ -f "$rc" ] || return 0
  if grep -qF 'elench: user bin' "$rc" 2>/dev/null; then
    say "PATH 已配置：$rc（跳过）"
    return 0
  fi
  say "写入 PATH：$rc"
  run "printf '\n# elench: user bin\nexport PATH=\"\$HOME/.local/bin:\$PATH\"\n' >> '$rc'"
}
add_path_line "$HOME/.bashrc"
add_path_line "$HOME/.zshrc"
add_path_line "$HOME/.profile"

# ---------------------------------------------------------------- 验证
say "验证"
if [ "$DRY" -eq 0 ]; then
  if [ -x "$BIN_DIR/elench" ]; then
    "$BIN_DIR/elench" --version || { say "✗ 验证失败：$BIN_DIR/elench --version"; exit 1; }
  else
    "$PY" "$REPO_DIR/src/elench.py" --version
  fi
else
  say "[dry-run] 跳过实际验证"
fi

cat <<'EOF'

  完成。下一步：

    1) 重开终端（或 source ~/.bashrc）
    2) 运行： elench --version
    3) 试试：  elench check <你的模块>.tu

  注意：如果 `which elench` 指向别的路径（例如 conda 里的旧版），
  把那个路径从 PATH 里删掉，或让 ~/.local/bin 排在前面。

EOF
