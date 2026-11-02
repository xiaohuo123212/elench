# docnums —— 直接 `make` 也能编（不依赖 CMake）
#
#   make          编译到 build/docnums
#   make test     与 Python 版逐字节对照
#   make clean
#
# 说明：
#   - `-static` 让产物是**单文件**（不依赖 libstdc++/libgcc DLL），
#     否则在别的机器上会报 0xC0000135（找不到 DLL）。
#   - Windows 上额外 `-lshell32`：用 `CommandLineToArgvW` 取 UTF-8 参数。

CXX      ?= g++
CXXFLAGS ?= -std=c++17 -O2 -Wall -Wextra
LDFLAGS  ?= -static
BUILD    ?= build

UNAME_S := $(shell uname -s 2>/dev/null)
ifeq ($(OS),Windows_NT)
  LDFLAGS += -lshell32
  EXT = .exe
else
  EXT =
endif

all: $(BUILD)/docnums$(EXT)

$(BUILD)/docnums$(EXT): main.cpp
	@mkdir -p $(BUILD)
	$(CXX) $(CXXFLAGS) $(LDFLAGS) -o $@ $<

test: all
	python parity.py $(BUILD)/docnums$(EXT)

clean:
	rm -rf $(BUILD)

.PHONY: all test clean
