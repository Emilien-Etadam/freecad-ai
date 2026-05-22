from freecad_ai.tools.macro_runner import resolve_macro_path


def test_safe_mode_resolves_fcmacro(tmp_path):
    (tmp_path / "Foo.FCMacro").write_text("print('hi')")
    path, err = resolve_macro_path("Foo", [str(tmp_path)], dangerous=False)
    assert err is None
    assert path == str(tmp_path / "Foo.FCMacro")


def test_safe_mode_resolves_py(tmp_path):
    (tmp_path / "Bar.py").write_text("print('hi')")
    path, err = resolve_macro_path("Bar", [str(tmp_path)], dangerous=False)
    assert err is None
    assert path == str(tmp_path / "Bar.py")


def test_safe_mode_refuses_absolute_path(tmp_path):
    path, err = resolve_macro_path("/etc/passwd", [str(tmp_path)], dangerous=False)
    assert path is None
    assert "dangerous" in err.lower()


def test_safe_mode_refuses_dotdot(tmp_path):
    path, err = resolve_macro_path("../escape", [str(tmp_path)], dangerous=False)
    assert path is None
    assert "dangerous" in err.lower()


def test_safe_mode_not_found(tmp_path):
    path, err = resolve_macro_path("Missing", [str(tmp_path)], dangerous=False)
    assert path is None
    assert "not found" in err.lower()


def test_dangerous_mode_allows_absolute_path(tmp_path):
    f = tmp_path / "anywhere.py"
    f.write_text("print(1)")
    path, err = resolve_macro_path(str(f), [], dangerous=True)
    assert err is None
    assert path == str(f)


def test_dangerous_mode_still_resolves_name(tmp_path):
    (tmp_path / "Named.FCMacro").write_text("print(1)")
    path, err = resolve_macro_path("Named", [str(tmp_path)], dangerous=True)
    assert err is None
    assert path == str(tmp_path / "Named.FCMacro")


def test_null_byte_rejected_safe_mode(tmp_path):
    path, err = resolve_macro_path("foo\x00bar", [str(tmp_path)], dangerous=False)
    assert path is None
    assert err  # clean error string, not a crash


def test_null_byte_rejected_dangerous_mode(tmp_path):
    path, err = resolve_macro_path("foo\x00bar", [str(tmp_path)], dangerous=True)
    assert path is None
    assert err
