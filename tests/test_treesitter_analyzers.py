"""Tests for tree-sitter JS/TS and Go analyzers."""
from pathlib import Path

import pytest

from codegraph_mcp.analyzers.base import SourceFile
from codegraph_mcp.analyzers.treesitter import (
    TREE_SITTER_AVAILABLE,
    TREE_SITTER_JVM_AVAILABLE,
    TreeSitterGoAnalyzer,
    TreeSitterJSAnalyzer,
)
from codegraph_mcp.graph.models import FunctionKind

pytestmark = pytest.mark.skipif(
    not TREE_SITTER_AVAILABLE, reason="tree-sitter not installed"
)


def js_file(text: str, lang: str = "javascript", name: str = "app.js") -> SourceFile:
    return SourceFile(
        repo_id="r", file_id="f", path=Path(name),
        relative_path=name, language=lang, text=text,
    )


def go_file(text: str, name: str = "pkg.go") -> SourceFile:
    return SourceFile(
        repo_id="r", file_id="g", path=Path(name),
        relative_path=name, language="go", text=text,
    )


# ── JavaScript ────────────────────────────────────────────────────────────────

class TestJSAnalyzer:
    def test_named_function(self):
        r = TreeSitterJSAnalyzer().analyze(js_file(
            "function greet(name) { return 'hello ' + name; }"
        ))
        assert len(r.functions) == 1
        fn = r.functions[0]
        assert fn.name == "greet"
        assert fn.kind == FunctionKind.function
        assert fn.is_async is False
        assert fn.confidence == 0.92

    def test_async_function(self):
        r = TreeSitterJSAnalyzer().analyze(js_file(
            "async function fetchUser(id) { return await db.get(id); }"
        ))
        assert r.functions[0].is_async is True

    def test_arrow_function(self):
        r = TreeSitterJSAnalyzer().analyze(js_file(
            "const add = (a, b) => a + b;"
        ))
        assert len(r.functions) == 1
        assert r.functions[0].name == "add"

    def test_class_and_methods(self):
        code = """
class UserService {
  constructor(db) { this.db = db; }
  async getUser(id) { return this.db.find(id); }
  deleteUser(id) { return this.db.remove(id); }
}
"""
        r = TreeSitterJSAnalyzer().analyze(js_file(code))
        names = {f.name for f in r.functions}
        assert names == {"constructor", "getUser", "deleteUser"}
        kinds = {f.kind for f in r.functions}
        assert FunctionKind.constructor in kinds
        assert FunctionKind.method in kinds
        assert len(r.classes) == 1
        assert r.classes[0].name == "UserService"

    def test_import_aware_edges(self):
        code = """
import { db } from './database';
function createUser(name) { return db.insert(name); }
"""
        r = TreeSitterJSAnalyzer().analyze(js_file(code))
        edge_targets = {e.target_symbol_name for e in r.edges}
        assert "./database.db.insert" in edge_targets

    def test_test_function_kind(self):
        # Jest test helpers are identified by file path (*.test.js / *.spec.js)
        # and by function names starting with "test" / "it" / "should"
        code = """
function testUserLogin() { return true; }
const shouldReturnUser = () => db.findUser(1);
"""
        r = TreeSitterJSAnalyzer().analyze(js_file(code, name="auth.test.js"))
        test_fns = [f for f in r.functions if f.is_test]
        assert len(test_fns) >= 1

    def test_spec_file_marks_all_as_test(self):
        code = "function createFixture() { return {}; }"
        r = TreeSitterJSAnalyzer().analyze(js_file(code, name="user.spec.ts"))
        # All functions in a .spec file are flagged as test
        assert all(f.is_test for f in r.functions)

    def test_typescript_types_extracted(self):
        code = """
export function greet(name: string): string { return name; }
"""
        r = TreeSitterJSAnalyzer().analyze(js_file(code, lang="typescript", name="a.ts"))
        fn = r.functions[0]
        assert fn.return_type == "string"
        assert fn.parameters_json[0]["type_hint"] == "string"

    def test_jsdoc_descriptor(self):
        code = """
/** Returns the sum of two numbers. */
function add(a, b) { return a + b; }
"""
        r = TreeSitterJSAnalyzer().analyze(js_file(code))
        desc = r.functions[0].descriptor
        assert desc is not None
        assert "sum" in (desc.summary or "").lower() or "sum" in (desc.raw or "").lower()

    def test_no_functions_empty_file(self):
        r = TreeSitterJSAnalyzer().analyze(js_file("// empty\n"))
        assert r.functions == []

    def test_complexity_counted(self):
        code = """
function check(x) {
  if (x > 0) {
    for (let i = 0; i < x; i++) {
      if (i % 2 === 0) { console.log(i); }
    }
  }
  return x;
}
"""
        r = TreeSitterJSAnalyzer().analyze(js_file(code))
        assert r.functions[0].complexity >= 3


# ── Go ────────────────────────────────────────────────────────────────────────

class TestGoAnalyzer:
    def test_function(self):
        r = TreeSitterGoAnalyzer().analyze(go_file(
            "package main\nfunc Add(a, b int) int { return a + b }\n"
        ))
        assert len(r.functions) == 1
        fn = r.functions[0]
        assert fn.name == "Add"
        assert fn.kind == FunctionKind.function
        assert fn.return_type == "int"
        assert fn.confidence == 0.94

    def test_method_with_receiver(self):
        code = """
package svc
type UserService struct{}
func (s *UserService) CreateUser(name string) error { return nil }
"""
        r = TreeSitterGoAnalyzer().analyze(go_file(code))
        methods = [f for f in r.functions if f.kind == FunctionKind.method]
        assert len(methods) == 1
        fn = methods[0]
        assert fn.name == "CreateUser"
        assert fn.qualified_name == "UserService.CreateUser"
        assert fn.enclosing_class == "UserService"
        assert fn.return_type == "error"

    def test_multiple_methods(self):
        code = """
package svc
type S struct{}
func (s *S) A() {}
func (s *S) B() {}
func (s *S) C() {}
func NewS() *S { return &S{} }
"""
        r = TreeSitterGoAnalyzer().analyze(go_file(code))
        names = {f.name for f in r.functions}
        assert names == {"A", "B", "C", "NewS"}
        methods = [f for f in r.functions if f.kind == FunctionKind.method]
        assert len(methods) == 3

    def test_go_doc_comment(self):
        code = """
package main
// ProcessPayment handles the payment flow for a given amount.
func ProcessPayment(amount float64) error { return nil }
"""
        r = TreeSitterGoAnalyzer().analyze(go_file(code))
        desc = r.functions[0].descriptor
        assert desc is not None
        assert "payment" in (desc.raw or "").lower()

    def test_call_edges(self):
        code = """
package main
import "fmt"
func Run() {
    result := compute(42)
    fmt.Println(result)
}
func compute(n int) int { return n * 2 }
"""
        r = TreeSitterGoAnalyzer().analyze(go_file(code))
        targets = {e.target_symbol_name for e in r.edges}
        assert "compute" in targets

    def test_import_aware_edges(self):
        code = """
package main
import "github.com/myorg/db"
func Load() { db.Query("select 1") }
"""
        r = TreeSitterGoAnalyzer().analyze(go_file(code))
        targets = {e.target_symbol_name for e in r.edges}
        assert any("Query" in t for t in targets)

    def test_test_function(self):
        code = """
package foo
import "testing"
func TestAdd(t *testing.T) {}
func BenchmarkAdd(b *testing.B) {}
"""
        r = TreeSitterGoAnalyzer().analyze(go_file(code, name="foo_test.go"))
        test_fns = [f for f in r.functions if f.is_test]
        assert len(test_fns) == 2

    def test_params_extracted(self):
        code = """
package main
func Transfer(from string, to string, amount float64) error { return nil }
"""
        r = TreeSitterGoAnalyzer().analyze(go_file(code))
        params = r.functions[0].parameters_json
        names = [p["name"] for p in params]
        assert "from" in names and "to" in names and "amount" in names


# ── Registry ──────────────────────────────────────────────────────────────────

class TestRegistry:
    def test_js_dispatches_to_treesitter(self):
        from codegraph_mcp.analyzers.registry import AnalyzerRegistry
        reg = AnalyzerRegistry()
        a = reg.get("javascript", Path("app.js"))
        assert isinstance(a, TreeSitterJSAnalyzer)

    def test_go_dispatches_to_treesitter(self):
        from codegraph_mcp.analyzers.registry import AnalyzerRegistry
        reg = AnalyzerRegistry()
        a = reg.get("go", Path("main.go"))
        assert isinstance(a, TreeSitterGoAnalyzer)

    def test_python_uses_own_analyzer(self):
        from codegraph_mcp.analyzers.generic import PythonAnalyzer
        from codegraph_mcp.analyzers.registry import AnalyzerRegistry
        reg = AnalyzerRegistry()
        a = reg.get("python", Path("app.py"))
        assert isinstance(a, PythonAnalyzer)


# ── Java / Rust / C# (requires treesitter-jvm extra) ─────────────────────────

jvm_only = pytest.mark.skipif(
    not TREE_SITTER_JVM_AVAILABLE, reason="tree-sitter-jvm extra not installed"
)


def java_file(text: str, name: str = "App.java") -> SourceFile:
    return SourceFile(
        repo_id="r", file_id="j", path=Path(name),
        relative_path=name, language="java", text=text,
    )


def rust_file(text: str, name: str = "lib.rs") -> SourceFile:
    return SourceFile(
        repo_id="r", file_id="rs", path=Path(name),
        relative_path=name, language="rust", text=text,
    )


def csharp_file(text: str, name: str = "Program.cs") -> SourceFile:
    return SourceFile(
        repo_id="r", file_id="cs", path=Path(name),
        relative_path=name, language="csharp", text=text,
    )


@jvm_only
class TestJavaAnalyzer:
    def _analyzer(self):
        from codegraph_mcp.analyzers.treesitter import TreeSitterJavaAnalyzer
        return TreeSitterJavaAnalyzer()

    def test_simple_method(self):
        code = """
public class UserService {
    public String getUser(int id) {
        return db.find(id);
    }
}
"""
        r = self._analyzer().analyze(java_file(code))
        assert len(r.functions) == 1
        fn = r.functions[0]
        assert fn.name == "getUser"
        assert fn.kind == FunctionKind.method
        assert fn.return_type == "String"
        assert fn.qualified_name == "UserService.getUser"
        assert fn.confidence == pytest.approx(0.93, abs=0.01)

    def test_constructor(self):
        code = """
public class Repo {
    private final Db db;
    public Repo(Db db) {
        this.db = db;
    }
}
"""
        r = self._analyzer().analyze(java_file(code))
        ctors = [f for f in r.functions if f.kind == FunctionKind.constructor]
        assert len(ctors) == 1
        assert ctors[0].name == "Repo"

    def test_multiple_methods_in_class(self):
        code = """
public class Calculator {
    public int add(int a, int b) { return a + b; }
    public int sub(int a, int b) { return a - b; }
    public static double sqrt(double x) { return Math.sqrt(x); }
}
"""
        r = self._analyzer().analyze(java_file(code))
        names = {f.name for f in r.functions}
        assert names == {"add", "sub", "sqrt"}
        assert all(f.enclosing_class == "Calculator" for f in r.functions)

    def test_async_void_method(self):
        code = """
public class Worker {
    public void processAsync() throws Exception {
        doWork();
    }
}
"""
        r = self._analyzer().analyze(java_file(code))
        assert len(r.functions) == 1
        assert r.functions[0].return_type == "void"

    def test_no_functions_empty_class(self):
        code = "public class Empty {}\n"
        r = self._analyzer().analyze(java_file(code))
        assert r.functions == []

    def test_registry_dispatches_java(self):
        from codegraph_mcp.analyzers.registry import AnalyzerRegistry
        from codegraph_mcp.analyzers.treesitter import TreeSitterJavaAnalyzer
        reg = AnalyzerRegistry()
        a = reg.get("java", Path("App.java"))
        assert isinstance(a, TreeSitterJavaAnalyzer)


@jvm_only
class TestRustAnalyzer:
    def _analyzer(self):
        from codegraph_mcp.analyzers.treesitter import TreeSitterRustAnalyzer
        return TreeSitterRustAnalyzer()

    def test_free_function(self):
        code = """
pub fn add(a: i32, b: i32) -> i32 {
    a + b
}
"""
        r = self._analyzer().analyze(rust_file(code))
        assert len(r.functions) == 1
        fn = r.functions[0]
        assert fn.name == "add"
        assert fn.kind == FunctionKind.function
        assert fn.return_type == "i32"
        assert fn.confidence == pytest.approx(0.94, abs=0.01)

    def test_impl_method(self):
        code = """
struct UserService {
    db: Db,
}

impl UserService {
    pub fn get_user(&self, id: u64) -> Option<User> {
        self.db.find(id)
    }
}
"""
        r = self._analyzer().analyze(rust_file(code))
        methods = [f for f in r.functions if f.kind == FunctionKind.method]
        assert len(methods) == 1
        fn = methods[0]
        assert fn.name == "get_user"
        assert fn.qualified_name == "UserService::get_user"
        assert fn.enclosing_class == "UserService"

    def test_async_function(self):
        code = """
pub async fn fetch_data(url: &str) -> Result<String, Error> {
    reqwest::get(url).await?.text().await
}
"""
        r = self._analyzer().analyze(rust_file(code))
        assert len(r.functions) == 1
        assert r.functions[0].is_async is True

    def test_multiple_impl_methods(self):
        code = """
impl Calculator {
    pub fn add(&self, a: f64, b: f64) -> f64 { a + b }
    pub fn mul(&self, a: f64, b: f64) -> f64 { a * b }
    fn helper(&self) -> bool { true }
}
"""
        r = self._analyzer().analyze(rust_file(code))
        names = {f.name for f in r.functions}
        assert names == {"add", "mul", "helper"}

    def test_no_functions(self):
        code = "struct Empty;\n"
        r = self._analyzer().analyze(rust_file(code))
        assert r.functions == []

    def test_registry_dispatches_rust(self):
        from codegraph_mcp.analyzers.registry import AnalyzerRegistry
        from codegraph_mcp.analyzers.treesitter import TreeSitterRustAnalyzer
        reg = AnalyzerRegistry()
        a = reg.get("rust", Path("lib.rs"))
        assert isinstance(a, TreeSitterRustAnalyzer)


@jvm_only
class TestCSharpAnalyzer:
    def _analyzer(self):
        from codegraph_mcp.analyzers.treesitter import TreeSitterCSharpAnalyzer
        return TreeSitterCSharpAnalyzer()

    def test_simple_method(self):
        code = """
public class UserService {
    public string GetUser(int id) {
        return db.Find(id);
    }
}
"""
        r = self._analyzer().analyze(csharp_file(code))
        assert len(r.functions) == 1
        fn = r.functions[0]
        assert fn.name == "GetUser"
        assert fn.kind == FunctionKind.method
        assert fn.return_type == "string"
        assert fn.qualified_name == "UserService.GetUser"
        assert fn.confidence == pytest.approx(0.93, abs=0.01)

    def test_constructor(self):
        code = """
public class Repo {
    private readonly Db _db;
    public Repo(Db db) {
        _db = db;
    }
}
"""
        r = self._analyzer().analyze(csharp_file(code))
        ctors = [f for f in r.functions if f.kind == FunctionKind.constructor]
        assert len(ctors) == 1
        assert ctors[0].name == "Repo"

    def test_async_method(self):
        code = """
public class Worker {
    public async Task<string> FetchAsync(string url) {
        return await httpClient.GetStringAsync(url);
    }
}
"""
        r = self._analyzer().analyze(csharp_file(code))
        assert len(r.functions) == 1
        fn = r.functions[0]
        assert fn.is_async is True
        assert fn.name == "FetchAsync"

    def test_multiple_methods(self):
        code = """
public class Calculator {
    public int Add(int a, int b) { return a + b; }
    protected int Sub(int a, int b) { return a - b; }
    private static double Sqrt(double x) { return Math.Sqrt(x); }
}
"""
        r = self._analyzer().analyze(csharp_file(code))
        names = {f.name for f in r.functions}
        assert names == {"Add", "Sub", "Sqrt"}
        assert all(f.enclosing_class == "Calculator" for f in r.functions)

    def test_no_functions_empty_class(self):
        code = "public class Empty {}\n"
        r = self._analyzer().analyze(csharp_file(code))
        assert r.functions == []

    def test_registry_dispatches_csharp(self):
        from codegraph_mcp.analyzers.registry import AnalyzerRegistry
        from codegraph_mcp.analyzers.treesitter import TreeSitterCSharpAnalyzer
        reg = AnalyzerRegistry()
        a = reg.get("csharp", Path("Program.cs"))
        assert isinstance(a, TreeSitterCSharpAnalyzer)
