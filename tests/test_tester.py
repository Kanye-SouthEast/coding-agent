"""tester：审查反例注入 prompt + 审查反馈的传递。"""

from __future__ import annotations

from coding_agent.config import settings


def test_tester_injects_review_feedback(monkeypatch):
    """reviewer 的具体反例必须注入 tester prompt，让 tester 显式测试该反例。"""
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")

    seen = {}

    def fake_json(prompt, model=None, max_tokens=None):
        seen["prompt"] = prompt
        return {"path": "test_t.py", "code": "print('PASS')"}

    monkeypatch.setattr(tester, "invoke_json", fake_json)
    monkeypatch.setattr(tester, "write_file", lambda ws, p, c: None)
    monkeypatch.setattr(tester, "run_command", lambda ws, cmd, language="": (0, "PASS"))

    state = {
        "requirement": "实现一个命令行计算器",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
        "review_feedback": "输入 '1..' 应按契约抛 ValueError，但代码未抛",
    }
    tester.tester_node(state)
    assert "审查反馈" in seen["prompt"]
    assert "1.." in seen["prompt"]  # 具体反例已注入 prompt


def test_tester_handles_missing_review_feedback(monkeypatch):
    """无审查反馈时（reviewer 通过/放行）也能正常格式化 prompt，不 KeyError。"""
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")

    def fake_json(prompt, model=None, max_tokens=None):
        return {"path": "test_t.py", "code": "print('PASS')"}

    monkeypatch.setattr(tester, "invoke_json", fake_json)
    monkeypatch.setattr(tester, "write_file", lambda ws, p, c: None)
    monkeypatch.setattr(tester, "run_command", lambda ws, cmd, language="": (0, "PASS"))

    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
    }
    out = tester.tester_node(state)
    assert out["test_result"] == "PASS"


def test_tester_injects_prev_failure_on_retry(monkeypatch):
    """tester 自身失败重试时，把上一次的报错注入 prompt，让模型自我纠正而非复现同样的错误。"""
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")

    seen = {}

    def fake_json(prompt, model=None, max_tokens=None):
        seen["prompt"] = prompt
        return {"path": "test_t.py", "code": "print('PASS')"}

    monkeypatch.setattr(tester, "invoke_json", fake_json)
    monkeypatch.setattr(tester, "write_file", lambda ws, p, c: None)
    monkeypatch.setattr(tester, "run_command", lambda ws, cmd, language="": (0, "PASS"))

    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
        "test_result": "FAIL: [tester] SyntaxError: unterminated string literal (test_suite.py, line 70)",
    }
    tester.tester_node(state)
    assert "SyntaxError" in seen["prompt"]
    assert "unterminated string literal" in seen["prompt"]


def test_tester_does_not_inject_code_failure(monkeypatch):
    """代码没通过测试（FAIL: 前缀，非 tester 故障）不注入 prev_failure，避免泄漏到 prompt。"""
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")

    seen = {}

    def fake_json(prompt, model=None, max_tokens=None):
        seen["prompt"] = prompt
        return {"path": "test_t.py", "code": "print('PASS')"}

    monkeypatch.setattr(tester, "invoke_json", fake_json)
    monkeypatch.setattr(tester, "write_file", lambda ws, p, c: None)
    monkeypatch.setattr(tester, "run_command", lambda ws, cmd, language="": (0, "PASS"))

    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending"}],
        "current_task_index": 0,
        "files": {},
        "test_result": "FAIL: [stderr] AssertionError: 3 != 4",
    }
    tester.tester_node(state)
    assert "AssertionError" not in seen["prompt"]


def test_extra_invalid_inputs_flags_wrong_layer():
    """tester 把别的层（如词法的 .5/1.）误判到本层时，对拍出「多出来的」条目。"""
    import coding_agent.agents.tester as tester

    task = {"invalid_inputs": ["括号不匹配", "缺操作数", "1.2.3"]}
    data = {"invalid_inputs": ["括号不匹配", "缺操作数", "1.2.3", ".5", "1."]}
    assert tester._extra_invalid_inputs(task, data) == [".5", "1."]


def test_extra_invalid_inputs_ok_when_verbatim():
    """tester 逐条照抄清单时无「多出来的」。"""
    import coding_agent.agents.tester as tester

    task = {"invalid_inputs": ["括号不匹配", "1.2.3"]}
    data = {"invalid_inputs": ["括号不匹配", "1.2.3"]}
    assert tester._extra_invalid_inputs(task, data) == []


def test_extra_invalid_inputs_skips_when_absent():
    """任一方缺 invalid_inputs 字段（旧 planner 数据）时跳过，不误伤。"""
    import coding_agent.agents.tester as tester

    assert tester._extra_invalid_inputs({}, {"invalid_inputs": ["1.2.3"]}) == []
    assert tester._extra_invalid_inputs({"invalid_inputs": ["1.2.3"]}, {}) == []


def test_extra_invalid_inputs_empty_expected_flags_all():
    """本任务无非法输入（清单为 []）时，tester 输出任何非法输入都是「多出来的」。"""
    import coding_agent.agents.tester as tester

    task = {"invalid_inputs": []}
    data = {"invalid_inputs": ["1.2.3"]}
    assert tester._extra_invalid_inputs(task, data) == ["1.2.3"]


def test_tester_node_flags_extra_invalid_inputs(monkeypatch):
    """tester 生成带「多出来」的非法输入用例时，走 FAIL: [tester] 回退重试。"""
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "python")

    def fake_json(prompt, model=None, max_tokens=None):
        return {
            "path": "test_t.py",
            "code": "print('PASS')",
            "invalid_inputs": ["1.2.3", ".5"],
        }

    monkeypatch.setattr(tester, "invoke_json", fake_json)
    monkeypatch.setattr(tester, "write_file", lambda ws, p, c: None)
    monkeypatch.setattr(tester, "run_command", lambda ws, cmd, language="": (0, "PASS"))

    state = {
        "requirement": "r",
        "tasks": [{"id": 1, "desc": "a", "deps": [], "status": "pending", "invalid_inputs": ["1.2.3"]}],
        "current_task_index": 0,
        "files": {},
    }
    out = tester.tester_node(state)
    assert out["test_result"].startswith("FAIL: [tester]")
    assert ".5" in out["test_result"]


def test_is_entry_task_detects_main_signature():
    """signature 含 main( 的任务判定为入口任务；普通函数签名则否。"""
    import coding_agent.agents.tester as tester

    assert tester._is_entry_task({"signature": "int main(int argc, char* argv[])"})
    assert tester._is_entry_task({"signature": "public static void main(String[] args)"})
    assert not tester._is_entry_task({"signature": "std::vector<Token> tokenize(const std::string&)"})
    assert not tester._is_entry_task({"signature": "double evaluate(std::vector<Token>)"})
    assert not tester._is_entry_task({})


def test_tester_node_cpp_entry_uses_exclude_command(monkeypatch):
    """C++ 入口任务：注入入口专用提示，且编译命令排除入口文件（不再 *.cpp 全量编译）。"""
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "cpp")

    seen = {}

    def fake_json(prompt, model=None, max_tokens=None):
        seen["prompt"] = prompt
        return {"path": "test_t.cpp", "code": "int main(){return 0;}"}

    def fake_run(ws, cmd, language=""):
        seen["cmd"] = cmd
        return (0, "PASS")

    monkeypatch.setattr(tester, "invoke_json", fake_json)
    monkeypatch.setattr(tester, "write_file", lambda ws, p, c: None)
    monkeypatch.setattr(tester, "run_command", fake_run)

    state = {
        "requirement": "实现命令行计算器入口",
        "tasks": [
            {
                "id": 3,
                "desc": "入口",
                "deps": [1, 2],
                "status": "pending",
                "signature": "int main(int argc, char* argv[])",
            }
        ],
        "current_task_index": 0,
        "files": {},
    }
    out = tester.tester_node(state)
    assert out["test_result"] == "PASS"
    assert "入口任务" in seen["prompt"]  # 注入入口专用提示
    assert "-not -name 'main.cpp'" in seen["cmd"]  # 编译命令排除入口文件
    assert "*.cpp {test}" not in seen["cmd"]  # 不再用 *.cpp 全量编译


def test_tester_node_cpp_normal_uses_glob_command(monkeypatch):
    """C++ 普通任务（非入口）仍用 *.cpp 全量编译，行为不变。"""
    import coding_agent.agents.tester as tester

    monkeypatch.setattr(settings, "mock", False)
    monkeypatch.setattr(settings, "language", "cpp")

    seen = {}

    def fake_json(prompt, model=None, max_tokens=None):
        seen["prompt"] = prompt
        return {"path": "test_t.cpp", "code": "int main(){return 0;}"}

    def fake_run(ws, cmd, language=""):
        seen["cmd"] = cmd
        return (0, "PASS")

    monkeypatch.setattr(tester, "invoke_json", fake_json)
    monkeypatch.setattr(tester, "write_file", lambda ws, p, c: None)
    monkeypatch.setattr(tester, "run_command", fake_run)

    state = {
        "requirement": "实现分词",
        "tasks": [
            {
                "id": 1,
                "desc": "分词",
                "deps": [],
                "status": "pending",
                "signature": "std::vector<Token> tokenize(const std::string&)",
            }
        ],
        "current_task_index": 0,
        "files": {},
    }
    out = tester.tester_node(state)
    assert out["test_result"] == "PASS"
    assert "入口任务" not in seen["prompt"]
    assert "*.cpp" in seen["cmd"]
    assert "-not -name" not in seen["cmd"]
