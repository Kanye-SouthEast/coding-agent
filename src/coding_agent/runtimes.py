"""语言运行时注册表：语言 → 沙箱镜像 + 测试器运行方式。

每种语言定义：镜像、文件后缀、测试运行命令模板、编码/测试提示。
新增语言只需在 RUNTIMES 里加一条（镜像需先 pull，如 `docker pull golang:1.23`）。

统一约定：
- 编码阶段只生成「可复用函数」，不写 main()/入口；
- 测试阶段生成独立的测试文件来引用这些函数并断言；
- 测试命令退出码 0 表示通过（编译型语言先编译再运行，编译失败同样算不通过）。

各语言遵循日常开发规范：
- C++：.h 声明 + .cpp 实现（include guard）
- Java：一个 public 类一个文件，文件名与类名一致
- Go：package + `go test` + `_test.go`
- Rust：cargo 库 crate（src/lib.rs）+ 集成测试（tests/）

字段说明：
- test_file：固定测试文件路径，统一放到 tests/ 子目录，避免跨任务残留的测试文件（各自含 main）
  互相冲突；Go 的 _test.go 需与代码同包，故留在包根目录；Rust 本就在 tests/。
- project_files：测试前先落盘的项目脚手架（如 Rust 的 Cargo.toml）
"""

from __future__ import annotations

RUNTIMES: dict[str, dict] = {
    "python": {
        "label": "Python 3.12",
        "image": "python:3.12-slim",
        "ext": "py",
        "run_test": "python -m tests.test_suite",
        "test_file": "tests/test_suite.py",
        "code_hint": "把可复用函数定义为模块级函数，其他文件可 import 复用。",
        "test_hint": "测试文件放在 tests/ 子目录（固定文件名 tests/test_suite.py）。被测函数必须 import 自代码文件：按 files 里的文件名去掉 .py 后缀作为模块名，如 `from calculator_engine import evaluate_expression`。严禁在测试脚本里重新实现被测函数，import 失败或函数缺失就直接断言失败（判 FAIL）。按上面「暴力参考对拍」的方式验证，最后一行 print('PASS') 表示通过。",
    },
    "javascript": {
        "label": "Node.js",
        "image": "node:24-slim",
        "ext": "js",
        "run_test": "node {test}",
        "test_file": "tests/test_suite.js",
        "code_hint": "把可复用函数用 module.exports 导出，其他文件可 require 复用。",
        "test_hint": "测试文件放在 tests/ 子目录（固定文件名 tests/test_suite.js），用 require('../<文件名>') 引入代码文件里的实际导出，按上面「暴力参考对拍」的方式验证，最后一行 console.log('PASS') 表示通过。",
    },
    "cpp": {
        "label": "C++17 (g++)",
        "image": "gcc:14",
        "ext": "cpp",
        "run_test": "g++ -std=c++17 -O2 -I. *.cpp {test} -o /tmp/test_bin && /tmp/test_bin",
        "test_file": "tests/main_test.cpp",
        # 入口任务（signature 含 main()）专用：编译时排除入口文件，避免业务 main 与测试脚本 main 符号冲突
        "entry_file": "main.cpp",
        "entry_run_test": "g++ -std=c++17 -O2 -I. $(find . -maxdepth 1 -name '*.cpp' -not -name 'main.cpp') {test} -o /tmp/test_bin && /tmp/test_bin",
        "code_hint": "遵循生产规范拆分：.h 头文件放类/函数声明（加 #pragma once 或 include guard），.cpp 实现文件 #include 自己的 .h 并写函数定义；一次输出一个 .h + 一个 .cpp，不要写 main()，其他任务 #include 你的 .h 复用。若本任务是入口任务（signature 要求 main()），把 main() 单独写在 main.cpp，其余复用逻辑仍按 .h/.cpp 拆分。",
        "test_hint": '测试文件放在 tests/ 子目录（固定文件名 tests/main_test.cpp）：按 files 里待测头文件的真实文件名 #include 它（声明在 .h，编译时带 -I. 指向工作区根目录），在 main() 里按上面「暴力参考对拍」的方式验证（写一个暴力参考实现对比），通过时 std::cout << "PASS" << std::endl，失败 return 非零。所有 .cpp 会一起编译链接。',
        "entry_test_hint": '【入口任务】本任务是可运行入口（main）。main 是全局入口、无法被测试脚本 import/调用，也不参与对拍（框架编译时会排除入口文件 main.cpp）。测试脚本只做轻量回归：include files 里复用函数的头文件（.h），按上面「暴力参考对拍」验证这些复用函数（如 tokenize/evaluate）的基本正确性。严禁尝试调用 main、严禁写模拟 argc/argv 的代码（char* argv[] = {"..."} 会把字符串字面量赋给非 const char*，触发编译警告）、严禁重新实现入口逻辑。入口的参数解析/返回码正确性由人工验证，不在本脚本内测。',
    },
    "java": {
        "label": "Java 21",
        "image": "eclipse-temurin:21-jdk",
        "ext": "java",
        "run_test": "javac -d /tmp/classes *.java {test} && java -cp /tmp/classes MainTest",
        "test_file": "tests/MainTest.java",
        "code_hint": "遵循生产规范：一个 public 类一个文件，文件名与 public 类名完全一致（类名 PascalCase）；把可复用函数写成 public static 方法，不要写 main()。",
        "test_hint": '测试文件放在 tests/ 子目录（固定文件名 tests/MainTest.java，public 类名 MainTest，与待测类同处默认包），在 main() 里按上面「暴力参考对拍」的方式验证（写一个暴力参考实现对比），通过时 System.out.println("PASS")，失败抛异常或 System.exit(非零)。',
    },
    "go": {
        "label": "Go 1.23",
        "image": "golang:1.23",
        "ext": "go",
        "run_test": "go mod init app >/dev/null 2>&1; go test . -v",
        "test_file": "main_test.go",
        "code_hint": "遵循生产规范：用 package main 写可复用函数（函数名大写即导出，命名遵循 Go 习惯），不要写 main()。",
        "test_hint": "写一个 _test.go 测试文件（package main，与待测代码同包），用 testing 包写 TestXxx，按上面「暴力参考对拍」的方式验证（在测试里写暴力参考实现对比），失败 t.Errorf/t.Fatalf；go test 退出码 0 表示通过。",
    },
    "rust": {
        "label": "Rust 1.80",
        "image": "rust:1.80-slim",
        "ext": "rs",
        "run_test": "cargo test",
        "test_file": "tests/main_test.rs",
        "project_files": {
            "Cargo.toml": """[package]
name = "app"
version = "0.1.0"
edition = "2021"
""",
        },
        "code_hint": "遵循 cargo 生产规范：把可复用函数写成 pub fn，放在 src/lib.rs（库 crate）；若 src/lib.rs 已存在，保留原有内容并追加新函数，不要写 main()。",
        "test_hint": "写一个集成测试文件（tests/main_test.rs），用 `use app::函数名;` 引入待测函数（crate 名为 app），写 #[test] fn，按上面「暴力参考对拍」的方式验证（在测试里写暴力参考实现对比）；cargo test 退出码 0 表示通过。",
    },
}

_ALIASES = {
    "py": "python",
    "python3": "python",
    "python3.12": "python",
    "js": "javascript",
    "node": "javascript",
    "nodejs": "javascript",
    "c++": "cpp",
    "cxx": "cpp",
    "cplusplus": "cpp",
    "golang": "go",
    "rs": "rust",
}


def normalize(language: str) -> str:
    """把语言名（含别名）归一化成 RUNTIMES 里的键，非法时抛 ValueError。"""
    lang = (language or "").strip().lower()
    if lang in RUNTIMES:
        return lang
    if lang in _ALIASES:
        return _ALIASES[lang]
    raise ValueError(f"不支持的语言 {language!r}，可用: {', '.join(RUNTIMES)}")


def get_runtime(language: str) -> dict:
    """返回某语言对应的运行时配置。"""
    return RUNTIMES[normalize(language)]
