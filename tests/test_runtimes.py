"""语言分发：normalize 别名归一化 + get_runtime 返回各语言的镜像/测试命令。"""

from __future__ import annotations

import pytest

from coding_agent.runtimes import get_runtime, normalize


def test_normalize_canonical():
    assert normalize("python") == "python"
    assert normalize("javascript") == "javascript"
    assert normalize("cpp") == "cpp"
    assert normalize("java") == "java"
    assert normalize("go") == "go"
    assert normalize("rust") == "rust"


def test_normalize_aliases():
    assert normalize("py") == "python"
    assert normalize("python3") == "python"
    assert normalize("js") == "javascript"
    assert normalize("node") == "javascript"
    assert normalize("nodejs") == "javascript"
    assert normalize("c++") == "cpp"
    assert normalize("cxx") == "cpp"
    assert normalize("golang") == "go"
    assert normalize("rs") == "rust"


def test_normalize_is_case_insensitive():
    assert normalize("JS") == "javascript"
    assert normalize("Python") == "python"
    assert normalize("C++") == "cpp"
    assert normalize("Go") == "go"


def test_normalize_unknown_raises():
    with pytest.raises(ValueError):
        normalize("ruby")


def test_get_runtime_python():
    rt = get_runtime("python")
    assert rt["image"] == "python:3.12-slim"
    assert rt["ext"] == "py"
    assert rt["test_file"] == "tests/test_suite.py"
    assert rt["run_test"] == "python -m tests.test_suite"


def test_get_runtime_javascript():
    rt = get_runtime("javascript")
    assert rt["image"] == "node:24-slim"
    assert rt["ext"] == "js"
    assert rt["test_file"] == "tests/test_suite.js"
    assert rt["run_test"].format(test="tests/test_suite.js") == "node tests/test_suite.js"


def test_get_runtime_cpp():
    rt = get_runtime("cpp")
    assert rt["image"] == "gcc:14"
    assert rt["ext"] == "cpp"
    assert rt["test_file"] == "tests/main_test.cpp"
    assert rt["run_test"] == "g++ -std=c++17 -O2 -I. *.cpp {test} -o /tmp/test_bin && /tmp/test_bin"


def test_get_runtime_cpp_entry():
    """C++ 入口任务有专用编译命令：排除入口文件 main.cpp，避免业务 main 与测试 main 符号冲突。"""
    rt = get_runtime("cpp")
    assert rt["entry_file"] == "main.cpp"
    # 编译命令必须显式排除入口文件 main.cpp（普通 run_test 的 *.cpp 会把业务 main 一起编译）
    assert "-not -name 'main.cpp'" in rt["entry_run_test"]
    # 专用测试提示已提供
    assert "入口任务" in rt["entry_test_hint"]


def test_get_runtime_java():
    rt = get_runtime("java")
    assert rt["image"] == "eclipse-temurin:21-jdk"
    assert rt["ext"] == "java"
    assert rt["test_file"] == "tests/MainTest.java"
    assert rt["run_test"] == "javac -d /tmp/classes *.java {test} && java -cp /tmp/classes MainTest"


def test_get_runtime_go():
    rt = get_runtime("go")
    assert rt["image"] == "golang:1.23"
    assert rt["ext"] == "go"
    assert rt["test_file"] == "main_test.go"
    assert rt["run_test"] == "go mod init app >/dev/null 2>&1; go test . -v"


def test_get_runtime_rust():
    rt = get_runtime("rust")
    assert rt["image"] == "rust:1.80-slim"
    assert rt["ext"] == "rs"
    assert rt["test_file"] == "tests/main_test.rs"
    assert rt["run_test"] == "cargo test"
    assert rt["project_files"]["Cargo.toml"].startswith("[package]")
    assert 'name = "app"' in rt["project_files"]["Cargo.toml"]
