#!/usr/bin/env bash
# Build the cpp_hal C++ sensor module from scratch.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
CPP_DIR="$PROJECT_DIR/cpp_sensors"
BUILD_DIR="$CPP_DIR/build"

PYTHON_EXE="$(uv run python -c "import sys; print(sys.executable)")"

echo "Cleaning build directory..."
rm -rf "$BUILD_DIR"
mkdir -p "$BUILD_DIR"

echo "Configuring CMake..."
cmake -S "$CPP_DIR" -B "$BUILD_DIR" -DPython_EXECUTABLE="$PYTHON_EXE"

echo "Building cpp_hal..."
cmake --build "$BUILD_DIR" -j"$(nproc)"

SO_FILE="$(find "$BUILD_DIR" -name 'cpp_hal*.so' -print -quit)"
cp "$SO_FILE" "$PROJECT_DIR/"
echo "Installed: $PROJECT_DIR/$(basename "$SO_FILE")"
